"""图谱存储访问层。

两个实现，接口完全一致：
- `Neo4jGraphRepository`：真实图谱。**所有 Cypher 都是本文件里的参数化模板**，
  模型只能通过 `GraphQueryService` 传参，永远拿不到拼 Cypher 的机会。
- `SampleGraphRepository`：读 `data/sample_graph.csv` 的内存实现。这是**降级路径**，
  让没有 Docker 的环境也能端到端跑通 Agent 链路。

数据模型（与策划书 5.1/5.2 对齐）：
    (:Entity { entity_key, source_id, entity_type, name_original, name_zh,
               source_dataset, description })
      -[:RELATED_TO { relation_code, display_relation_original, source_dataset }]->
    (:Entity)
"""

import csv
import difflib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

# --------------------------------------------------------------------------- #
# Cypher 模板（唯一允许出现 Cypher 的地方；全部参数化，不允许字符串拼接参数）
# --------------------------------------------------------------------------- #
_CYPHER_SEARCH = """
MATCH (e:Entity)
WHERE ($entity_type IS NULL OR e.entity_type = $entity_type)
  AND (toLower(e.name_original) CONTAINS toLower($q)
       OR e.name_zh CONTAINS $q
       OR toLower(e.entity_key) CONTAINS toLower($q))
RETURN e.entity_key AS entity_key, e.name_original AS name_original,
       e.name_zh AS name_zh, e.entity_type AS entity_type,
       e.source_id AS source_id, e.source_dataset AS source_dataset
ORDER BY size(e.name_original)
LIMIT $limit
"""

_CYPHER_ENTITY = """
MATCH (e:Entity {entity_key: $key})
OPTIONAL MATCH (e)-[r:RELATED_TO]-()
WITH e, count(r) AS degree
RETURN e.entity_key AS entity_key, e.name_original AS name_original,
       e.name_zh AS name_zh, e.entity_type AS entity_type,
       e.source_id AS source_id, e.source_dataset AS source_dataset,
       e.description AS description, degree
"""

_CYPHER_REL_OUT = """
MATCH (e:Entity {entity_key: $key})-[r:RELATED_TO]->(o:Entity)
WHERE ($relation_code IS NULL OR r.relation_code = $relation_code)
RETURN e.entity_key AS src_key, e.name_original AS src_name,
       e.source_dataset AS src_source,
       r.relation_code AS relation_code,
       r.display_relation_original AS relation_display,
       o.entity_key AS dst_key, o.name_original AS dst_name,
       o.name_zh AS dst_name_zh, o.entity_type AS dst_type,
       o.source_dataset AS dst_source
ORDER BY o.entity_key
LIMIT $limit
"""

_CYPHER_REL_IN = """
MATCH (e:Entity {entity_key: $key})<-[r:RELATED_TO]-(o:Entity)
WHERE ($relation_code IS NULL OR r.relation_code = $relation_code)
RETURN o.entity_key AS src_key, o.name_original AS src_name,
       o.source_dataset AS src_source,
       r.relation_code AS relation_code,
       r.display_relation_original AS relation_display,
       e.entity_key AS dst_key, e.name_original AS dst_name,
       e.name_zh AS dst_name_zh, e.entity_type AS dst_type,
       e.source_dataset AS dst_source
ORDER BY o.entity_key
LIMIT $limit
"""

# 关系方向模板只在这三个里选，避免任何形式的自由拼接
_CYPHER_REL_BY_DIRECTION = {"out": _CYPHER_REL_OUT, "in": _CYPHER_REL_IN}

# 关系类型分布：枢纽实体（度数可达数百）只取前 N 条边会混入多种关系，
# 调用方需要先知道节点上有哪些关系类型以及各自规模，再决定查哪一类。
_CYPHER_REL_COUNTS = {
    "out": """
MATCH (e:Entity {entity_key: $key})-[r:RELATED_TO]->(o:Entity)
RETURN r.relation_code AS relation_code, count(r) AS cnt
ORDER BY cnt DESC
""",
    "in": """
MATCH (e:Entity {entity_key: $key})<-[r:RELATED_TO]-(o:Entity)
RETURN r.relation_code AS relation_code, count(r) AS cnt
ORDER BY cnt DESC
""",
    "both": """
MATCH (e:Entity {entity_key: $key})-[r:RELATED_TO]-(o:Entity)
RETURN r.relation_code AS relation_code, count(r) AS cnt
ORDER BY cnt DESC
""",
}

_CYPHER_SUBGRAPH_NODES = """
MATCH (e:Entity {entity_key: $key})
OPTIONAL MATCH (e)-[:RELATED_TO*1..__DEPTH__]-(m:Entity)
WITH e, collect(DISTINCT m) AS others
RETURN e AS center, others[0..$node_limit] AS others
"""

_CYPHER_SUBGRAPH_EDGES = """
MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity)
WHERE a.entity_key IN $keys AND b.entity_key IN $keys
RETURN a.entity_key AS src_key, a.name_original AS src_name,
       a.source_dataset AS src_source,
       r.relation_code AS relation_code,
       r.display_relation_original AS relation_display,
       b.entity_key AS dst_key, b.name_original AS dst_name,
       b.name_zh AS dst_name_zh, b.entity_type AS dst_type,
       b.source_dataset AS dst_source
ORDER BY a.entity_key, b.entity_key
LIMIT $limit
"""

# 关系跳数上限无法用 driver 参数化，因此用「已校验的整数」替换模板占位符（1..3）。
# 注意：模板里有 Cypher map 字面量 { ... }，不能用 str.format，只能用 replace 占位符。
_CYPHER_PATH = """
MATCH (a:Entity {entity_key: $src}), (b:Entity {entity_key: $dst})
MATCH p = shortestPath((a)-[:RELATED_TO*1..__HOPS__]-(b))
RETURN [n IN nodes(p) | {entity_key: n.entity_key, name_original: n.name_original,
                         name_zh: n.name_zh, entity_type: n.entity_type,
                         source_dataset: n.source_dataset}] AS nodes,
       [r IN relationships(p) | {relation_code: r.relation_code,
                                 display_relation_original: r.display_relation_original,
                                 source_dataset: r.source_dataset}] AS rels
"""


class GraphRepository(ABC):
    """图谱查询接口。所有方法只读，不做业务判断。"""

    backend: str = "unknown"

    @abstractmethod
    def ping(self) -> bool:
        """后端是否可用。"""

    @abstractmethod
    def search_entity(
        self, query: str, entity_type: Optional[str], limit: int
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def get_entity(self, entity_key: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def get_relations(
        self,
        entity_key: str,
        relation_code: Optional[str],
        direction: str,
        limit: int,
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def count_relations(
        self, entity_key: str, direction: str
    ) -> List[Dict[str, Any]]:
        """返回 [{relation_code, cnt}]，该实体在该方向上各关系类型的条数（不受 limit 影响）。"""
        ...

    @abstractmethod
    def find_path(
        self, src_key: str, dst_key: str, max_hops: int
    ) -> Dict[str, Any]: ...

    @abstractmethod
    def get_subgraph(
        self, entity_key: str, depth: int, node_limit: int
    ) -> Dict[str, Any]: ...

    def close(self) -> None:  # pragma: no cover
        return None


# --------------------------------------------------------------------------- #
# Neo4j 实现
# --------------------------------------------------------------------------- #
class Neo4jGraphRepository(GraphRepository):
    backend = "neo4j"

    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j"):
        from neo4j import GraphDatabase  # 延迟导入：未装驱动时不影响降级路径

        self._database = database
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    def ping(self) -> bool:
        try:
            self._driver.verify_connectivity()
            return True
        except Exception:
            return False

    def _run(self, cypher: str, **params: Any) -> List[Dict[str, Any]]:
        with self._driver.session(database=self._database) as session:
            return [record.data() for record in session.run(cypher, **params)]

    def search_entity(self, query, entity_type, limit):
        return self._run(
            _CYPHER_SEARCH, q=query, entity_type=entity_type, limit=limit
        )

    def get_entity(self, entity_key):
        rows = self._run(_CYPHER_ENTITY, key=entity_key)
        return rows[0] if rows else None

    def get_relations(self, entity_key, relation_code, direction, limit):
        if direction == "both":
            out = self._run(
                _CYPHER_REL_BY_DIRECTION["out"],
                key=entity_key,
                relation_code=relation_code,
                limit=limit,
            )
            inn = self._run(
                _CYPHER_REL_BY_DIRECTION["in"],
                key=entity_key,
                relation_code=relation_code,
                limit=limit,
            )
            return [{**row, "direction": "out"} for row in out] + [
                {**row, "direction": "in"} for row in inn
            ]
        rows = self._run(
            _CYPHER_REL_BY_DIRECTION[direction],
            key=entity_key,
            relation_code=relation_code,
            limit=limit,
        )
        return [{**row, "direction": direction} for row in rows]

    def count_relations(self, entity_key, direction):
        return self._run(_CYPHER_REL_COUNTS[direction], key=entity_key)

    def find_path(self, src_key, dst_key, max_hops):
        hops = max(1, min(int(max_hops), 3))
        rows = self._run(
            _CYPHER_PATH.replace("__HOPS__", str(hops)), src=src_key, dst=dst_key
        )
        if not rows:
            return {"found": False, "hops": hops, "nodes": [], "edges": []}
        row = rows[0]
        edges = _pair_edges(row["nodes"], row["rels"])
        return {"found": True, "hops": hops, "nodes": row["nodes"], "edges": edges}

    def get_subgraph(self, entity_key, depth, node_limit):
        d = max(1, min(int(depth), 2))
        rows = self._run(
            _CYPHER_SUBGRAPH_NODES.replace("__DEPTH__", str(d)),
            key=entity_key,
            node_limit=node_limit,
        )
        if not rows:
            return {"nodes": [], "edges": []}
        center = rows[0]["center"]
        others = [n for n in rows[0]["others"] if n is not None]
        nodes = [center] + others
        keys = [n["entity_key"] for n in nodes]
        edges = [
            {**row, "direction": "out"}
            for row in self._run(
                _CYPHER_SUBGRAPH_EDGES, keys=keys, limit=node_limit * 2
            )
        ]
        return {"nodes": nodes, "edges": edges}

    def close(self):
        try:
            self._driver.close()
        except Exception:
            pass


def _pair_edges(nodes: Sequence[Dict[str, Any]], rels: Sequence[Dict[str, Any]]):
    """把路径上「无方向的节点序列 + 无端点的关系序列」配成带端点的一条条边。"""
    edges: List[Dict[str, Any]] = []
    for i, rel in enumerate(rels):
        a, b = nodes[i], nodes[i + 1]
        edges.append(
            {
                "src_key": a["entity_key"],
                "src_name": a.get("name_original", ""),
                "src_source": a.get("source_dataset", ""),
                "dst_key": b["entity_key"],
                "dst_name": b.get("name_original", ""),
                "dst_name_zh": b.get("name_zh", ""),
                "dst_type": b.get("entity_type", ""),
                "dst_source": b.get("source_dataset", ""),
                "relation_code": rel.get("relation_code", ""),
                "relation_display": rel.get("display_relation_original", ""),
                "direction": "out",
            }
        )
    return edges


# --------------------------------------------------------------------------- #
# 样例数据实现（降级路径）
# --------------------------------------------------------------------------- #
class SampleGraphRepository(GraphRepository):
    """从 data/sample_graph.csv 载入的只读内存图谱。

    CSV 每行是一条有向关系，同时冗余携带了两端实体的属性；载入时按 entity_key 去重。
    """

    backend = "sample"

    def __init__(self, csv_path: str):
        self._path = Path(csv_path)
        if not self._path.exists():
            raise FileNotFoundError(f"样例图谱文件不存在: {self._path}")
        self._entities: Dict[str, Dict[str, Any]] = {}
        self._edges: List[Dict[str, Any]] = []
        self._degree: Dict[str, int] = {}
        self._load()

    def _put_entity(self, key, name_original, name_zh, etype, desc, dataset):
        if key in self._entities:
            return
        self._entities[key] = {
            "entity_key": key,
            "source_id": key,
            "name_original": name_original,
            "name_zh": name_zh,
            "entity_type": etype,
            "description": desc,
            "source_dataset": dataset,
        }

    def _load(self) -> None:
        with self._path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                dataset = row.get("source_dataset", "SampleKG")
                self._put_entity(
                    row["src_key"],
                    row["src_name_original"],
                    row["src_name_zh"],
                    row["src_type"],
                    row["src_desc"],
                    dataset,
                )
                self._put_entity(
                    row["dst_key"],
                    row["dst_name_original"],
                    row["dst_name_zh"],
                    row["dst_type"],
                    row["dst_desc"],
                    dataset,
                )
                self._edges.append(
                    {
                        "src_key": row["src_key"],
                        "src_name": row["src_name_original"],
                        "src_name_zh": row["src_name_zh"],
                        "src_source": dataset,
                        "relation_code": row["relation_code"],
                        "relation_display": row["relation_display"],
                        "source_dataset": dataset,
                        "dst_key": row["dst_key"],
                        "dst_name": row["dst_name_original"],
                        "dst_name_zh": row["dst_name_zh"],
                        "dst_source": dataset,
                        "dst_type": row["dst_type"],
                        "direction": "out",
                    }
                )
        for edge in self._edges:
            self._degree[edge["src_key"]] = self._degree.get(edge["src_key"], 0) + 1
            self._degree[edge["dst_key"]] = self._degree.get(edge["dst_key"], 0) + 1

    def ping(self) -> bool:
        return bool(self._entities)

    def _brief(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "entity_key": entity["entity_key"],
            "name_original": entity["name_original"],
            "name_zh": entity["name_zh"],
            "entity_type": entity["entity_type"],
            "source_id": entity["source_id"],
            "source_dataset": entity["source_dataset"],
        }

    def _score(self, entity: Dict[str, Any], q: str) -> Optional[float]:
        ql = q.lower()
        originals = [
            entity["name_original"].lower(),
            entity["name_zh"],
            entity["entity_key"].lower(),
        ]
        if ql in originals or q in originals:
            return 0.0
        for text in originals:
            if text.startswith(ql):
                return 1.0
            if ql in text or q in text:
                return 2.0
        return None

    def search_entity(self, query, entity_type, limit):
        q = (query or "").strip()
        pool = [
            e
            for e in self._entities.values()
            if not entity_type or e["entity_type"] == entity_type
        ]
        hits: List[tuple] = []
        if q:
            for entity in pool:
                score = self._score(entity, q)
                if score is not None:
                    hits.append((score, entity))
            if not hits:
                # 兜底：拼写近似匹配（纯本地，不消耗任何额度）
                names = {e["name_original"].lower(): e for e in pool}
                for close in difflib.get_close_matches(q.lower(), names.keys(), n=limit):
                    hits.append((3.0, names[close]))
        else:
            hits = [(0.0, e) for e in pool]
        hits.sort(key=lambda item: (item[0], len(item[1]["name_original"])))
        return [self._brief(e) for _, e in hits[:limit]]

    def get_entity(self, entity_key):
        entity = self._entities.get(entity_key)
        if entity is None:
            return None
        return {**entity, "degree": self._degree.get(entity_key, 0)}

    def get_relations(self, entity_key, relation_code, direction, limit):
        rows: List[Dict[str, Any]] = []
        for edge in self._edges:
            if relation_code and edge["relation_code"] != relation_code:
                continue
            if edge["src_key"] == entity_key and direction in ("out", "both"):
                rows.append({**edge, "direction": "out"})
            elif edge["dst_key"] == entity_key and direction in ("in", "both"):
                rows.append({**edge, "direction": "in"})
        rows.sort(key=lambda r: (r["dst_key"] if r["direction"] == "out" else r["src_key"]))
        return rows[:limit]

    def count_relations(self, entity_key: str, direction: str):
        counts: Dict[str, int] = {}
        for edge in self._edges:
            if edge["src_key"] == entity_key and direction in ("out", "both"):
                counts[edge["relation_code"]] = counts.get(edge["relation_code"], 0) + 1
            elif edge["dst_key"] == entity_key and direction in ("in", "both"):
                counts[edge["relation_code"]] = counts.get(edge["relation_code"], 0) + 1
        return [
            {"relation_code": code, "cnt": n}
            for code, n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        ]

    def _neighbors(self, key: str) -> Iterable[tuple]:
        for edge in self._edges:
            if edge["src_key"] == key:
                yield edge["dst_key"], edge
            elif edge["dst_key"] == key:
                yield edge["src_key"], edge

    def find_path(self, src_key, dst_key, max_hops):
        hops = max(1, min(int(max_hops), 3))
        if src_key not in self._entities or dst_key not in self._entities:
            return {"found": False, "hops": hops, "nodes": [], "edges": []}
        # 无向 BFS：两点间最短关系路径
        queue: List[tuple] = [(src_key, [src_key], [])]
        seen = {src_key}
        while queue:
            current, path, edges = queue.pop(0)
            if len(edges) >= hops:
                continue
            for nxt, edge in self._neighbors(current):
                if nxt in path:
                    continue
                new_edges = [*edges, dict(edge)]
                if nxt == dst_key:
                    nodes = [self._entities[k] for k in [*path, nxt]]
                    return {
                        "found": True,
                        "hops": hops,
                        "nodes": [self._brief(n) for n in nodes],
                        "edges": new_edges,
                    }
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, [*path, nxt], new_edges))
        return {"found": False, "hops": hops, "nodes": [], "edges": []}

    def get_subgraph(self, entity_key, depth, node_limit):
        if entity_key not in self._entities:
            return {"nodes": [], "edges": []}
        d = max(1, min(int(depth), 2))
        collected = {entity_key}
        frontier = {entity_key}
        for _ in range(d):
            nxt = set()
            for key in frontier:
                for neighbor, _edge in self._neighbors(key):
                    if neighbor not in collected:
                        nxt.add(neighbor)
            collected |= nxt
            frontier = nxt
            if len(collected) >= node_limit:
                break
        keys = list(collected)[:node_limit]
        key_set = set(keys)
        nodes = [self._brief(self._entities[k]) for k in keys]
        edges = [
            {**e, "direction": "out"}
            for e in self._edges
            if e["src_key"] in key_set and e["dst_key"] in key_set
        ]
        return {"nodes": nodes, "edges": edges}
