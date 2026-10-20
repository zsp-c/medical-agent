"""图谱查询业务层。

职责：
1. 选后端（Neo4j 优先，连不上自动降级到样例数据），并对外屏蔽差异
2. **强制白名单上限**：截断必须显式、不能静默丢数据
3. 把仓储层原始行转成固定契约（GraphResult），便于前端直接消费

铁律：模型永远不能自由拼 Cypher —— 模型只选工具、填参数，
Cypher 只存在于 `graph_repository.py` 的参数化模板里。
"""

from typing import Any, Callable, Dict, List, Optional

import functools

from app.repositories.graph_repository import (
    GraphRepository,
    Neo4jGraphRepository,
    SampleGraphRepository,
)
from app.schemas import EntityBrief, GraphResult, RelationEdge
from app.settings import Settings

SEARCH_LIMIT_DEFAULT, SEARCH_LIMIT_MAX = 10, 20
RELATION_LIMIT_DEFAULT, RELATION_LIMIT_MAX = 20, 50
PATH_MAX_HOPS = 3
SUBGRAPH_DEPTH_MAX, SUBGRAPH_NODES_MAX = 2, 50


def _clamp(value: int, low: int, high: int, default: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


def _tool_event(name: str):
    """方法级工具事件装饰器：进入发 start、退出（含异常）发 end。"""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            self._emit_tool(name, "start")
            try:
                return fn(self, *args, **kwargs)
            finally:
                self._emit_tool(name, "end")

        return wrapper

    return deco


def build_graph_repository(settings: Settings) -> GraphRepository:
    """按配置挑后端；auto = 先试 Neo4j，失败则降级样例数据。"""
    backend = settings.graph_backend
    if backend in ("neo4j", "auto") and settings.neo4j_password:
        try:
            repo = Neo4jGraphRepository(
                settings.neo4j_uri,
                settings.neo4j_user,
                settings.neo4j_password,
                settings.neo4j_database,
            )
            if repo.ping():
                return repo
            repo.close()
        except Exception:
            pass
        if backend == "neo4j":
            raise RuntimeError(
                f"配置要求 neo4j 后端，但连接失败: {settings.neo4j_uri}"
            )
    return SampleGraphRepository(settings.sample_graph)


def _to_brief(row: Dict[str, Any], name_key: str = "name_original") -> EntityBrief:
    return EntityBrief(
        entity_key=row.get("entity_key") or row.get("dst_key", ""),
        name=row.get(name_key, ""),
        name_zh=row.get("name_zh", ""),
        entity_type=row.get("entity_type") or row.get("dst_type", ""),
        source_id=row.get("source_id", ""),
    )


class GraphQueryService:
    """5 个白名单图谱查询的统一入口。

    `repo` 可从外部注入（BYOK 池共享同一个 Neo4j 连接），此时 `close()` 不再负责释放它；
    证据收集器是本实例的状态，**每个 Agent 必须持有自己的 GraphQueryService**。
    """

    def __init__(
        self, settings: Settings, repo: Optional[GraphRepository] = None
    ) -> None:
        self._repo = repo or build_graph_repository(settings)
        self._owns_repo = repo is None
        # 本轮已引用的实体，供 AgentService 组装 evidence_entities。
        # 每轮请求串行执行（受 LLM 速率限制约束），因此用实例态是安全的。
        self._evidence: List[EntityBrief] = []
        self._seen_keys: set = set()
        # 工具事件钩子（流式模式用）：hook(tool_name, phase)，phase ∈ {"start", "end"}
        self._tool_hook: Optional[Callable[[str, str], None]] = None

    # ---- 工具事件钩子 ----
    def set_tool_hook(self, hook: Optional[Callable[[str, str], None]]) -> None:
        """设置/清除工具事件钩子。与证据收集器同为实例态，随 begin_round 的轮次使用。"""
        self._tool_hook = hook

    def _emit_tool(self, name: str, phase: str) -> None:
        hook = self._tool_hook
        if hook is not None:
            try:
                hook(name, phase)
            except Exception:
                pass  # 事件推送失败不影响工具本体

    # ---- 生命周期 ----
    @property
    def backend(self) -> str:
        return self._repo.backend

    def close(self) -> None:
        if self._owns_repo:
            self._repo.close()

    def begin_round(self) -> None:
        """每轮对话开始前清空证据收集器。"""
        self._evidence = []
        self._seen_keys = set()

    @property
    def evidence(self) -> List[EntityBrief]:
        return list(self._evidence)

    def _record(self, briefs: List[EntityBrief]) -> None:
        for brief in briefs:
            if brief.entity_key and brief.entity_key not in self._seen_keys:
                self._seen_keys.add(brief.entity_key)
                self._evidence.append(brief)

    # ---- 5 个白名单能力 ----
    @_tool_event("search_entity")
    def search_entity(
        self,
        query: str,
        entity_type: Optional[str] = None,
        limit: int = SEARCH_LIMIT_DEFAULT,
    ) -> GraphResult:
        """按名称/中文名/entity_key 模糊搜索实体。"""
        capped = _clamp(limit, 1, SEARCH_LIMIT_MAX, SEARCH_LIMIT_DEFAULT)
        rows = self._repo.search_entity(query or "", entity_type or None, capped)
        nodes = [_to_brief(row) for row in rows]
        self._record(nodes)
        return GraphResult(
            nodes=nodes,
            truncated=len(rows) >= capped,
            meta={"backend": self._repo.backend, "query": query, "limit": capped},
        )

    @_tool_event("get_entity_details")
    def get_entity_details(self, entity_key: str) -> Dict[str, Any]:
        """单实体属性 + 邻居度数。"""
        row = self._repo.get_entity(entity_key)
        if row is None:
            return {"found": False, "entity_key": entity_key, "backend": self._repo.backend}
        brief = _to_brief(row)
        self._record([brief])
        return {
            "found": True,
            "backend": self._repo.backend,
            "entity_key": row.get("entity_key", entity_key),
            "name": row.get("name_original", ""),
            "name_zh": row.get("name_zh", ""),
            "entity_type": row.get("entity_type", ""),
            "source_id": row.get("source_id", ""),
            "source_dataset": row.get("source_dataset", ""),
            "description": row.get("description", ""),
            "degree": row.get("degree", 0),
        }

    @_tool_event("query_relations")
    def query_relations(
        self,
        entity_key: str,
        relation_code: Optional[str] = None,
        direction: str = "both",
        limit: int = RELATION_LIMIT_DEFAULT,
    ) -> GraphResult:
        """一跳关系。direction ∈ {out, in, both}。"""
        direction = direction if direction in ("out", "in", "both") else "both"
        capped = _clamp(limit, 1, RELATION_LIMIT_MAX, RELATION_LIMIT_DEFAULT)
        rows = self._repo.get_relations(entity_key, relation_code or None, direction, capped)
        edges = [
            RelationEdge(
                src_key=row.get("src_key", ""),
                src_name=row.get("src_name", ""),
                src_source=row.get("src_source", ""),
                relation_code=row.get("relation_code", ""),
                relation_display=row.get("relation_display", ""),
                dst_key=row.get("dst_key", ""),
                dst_name=row.get("dst_name", ""),
                dst_type=row.get("dst_type", ""),
                dst_source=row.get("dst_source", ""),
                direction=row.get("direction", "out"),
            )
            for row in rows
        ]
        nodes = [
            EntityBrief(
                entity_key=row.get("dst_key", ""),
                name=row.get("dst_name", ""),
                name_zh=row.get("dst_name_zh", ""),
                entity_type=row.get("dst_type", ""),
            )
            for row in rows
        ]
        self._record(nodes)
        relation_counts = {
            row.get("relation_code", ""): row.get("cnt", 0)
            for row in self._repo.count_relations(entity_key, direction)
        }
        return GraphResult(
            nodes=nodes,
            edges=edges,
            truncated=len(rows) >= capped,
            meta={
                "backend": self._repo.backend,
                "entity_key": entity_key,
                "direction": direction,
                "limit": capped,
                "relation_counts": relation_counts,
            },
        )

    @_tool_event("find_relation_path")
    def find_relation_path(
        self, src_key: str, dst_key: str, max_hops: int = 2
    ) -> Dict[str, Any]:
        """两点间最短关系路径（无向，最多 PATH_MAX_HOPS 跳）。"""
        hops = _clamp(max_hops, 1, PATH_MAX_HOPS, 2)
        result = self._repo.find_path(src_key, dst_key, hops)
        nodes = [_to_brief(row) for row in result.get("nodes", [])]
        self._record(nodes)
        return {
            "found": result.get("found", False),
            "backend": self._repo.backend,
            "src_key": src_key,
            "dst_key": dst_key,
            "max_hops": hops,
            "nodes": nodes,
            "edges": result.get("edges", []),
        }

    @_tool_event("get_subgraph")
    def get_subgraph(
        self, entity_key: str, depth: int = 1, limit: int = SUBGRAPH_NODES_MAX
    ) -> GraphResult:
        """多跳子图。depth ≤ 2，节点总数 ≤ 50。"""
        d = _clamp(depth, 1, SUBGRAPH_DEPTH_MAX, 1)
        node_limit = _clamp(limit, 1, SUBGRAPH_NODES_MAX, SUBGRAPH_NODES_MAX)
        result = self._repo.get_subgraph(entity_key, d, node_limit)
        nodes = [_to_brief(row) for row in result.get("nodes", [])]
        edges = [
            RelationEdge(
                src_key=row.get("src_key", ""),
                src_name=row.get("src_name", ""),
                src_source=row.get("src_source", ""),
                relation_code=row.get("relation_code", ""),
                relation_display=row.get("relation_display", ""),
                dst_key=row.get("dst_key", ""),
                dst_name=row.get("dst_name", ""),
                dst_type=row.get("dst_type", ""),
                dst_source=row.get("dst_source", ""),
                direction=row.get("direction", "out"),
            )
            for row in result.get("edges", [])
        ]
        self._record(nodes)
        truncated = len(result.get("nodes", [])) >= node_limit
        return GraphResult(
            nodes=nodes,
            edges=edges,
            truncated=truncated,
            meta={
                "backend": self._repo.backend,
                "entity_key": entity_key,
                "depth": d,
                "limit": node_limit,
            },
        )
