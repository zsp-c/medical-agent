"""把 OpenCMKG 全量导入 Neo4j（替代原 PrimeKG-Plus 导入）。

数据来源：https://github.com/RuiqingDing/OpenCMKG （仅学术研究用）
- `data/opencmkg/triples.txt`：354,755 条三元组，每行「实体1,关系,实体2」（关系为 13 种英文码）
- `data/opencmkg/entities_dict.txt`：Python 字面量 dict，类别 → 实体名列表（7 类）

设计要点：
- 实体键 `CMKG:<entity_type>:<中文名>`；中文名同时写 name_original 与 name_zh（全中文图谱，无需翻译）。
- 实体类型以 entities_dict 为准；字典查不到的实体按关系语义推断（如 disease_has_symptom
  的起点是 disease、终点是 symptom），推断不出落 `unknown`。
- 行解析不假设实体名无逗号：先按逗号切开，找第一段命中 13 种已知关系码的位置，
  前后片段各自拼回（实测存在带标点实体名）。
- 写入模型与 `scripts/import_sample.py` 完全一致（`Entity` / `RELATED_TO`），
  GraphQueryService 与图谱工具零改动。
- 默认先分批清空已有 :Entity（先删关系再删节点，避免单事务内存上限）。
- `--sample-out` 可从解析结果导出降级样例 CSV（供 GRAPH_BACKEND=sample）。

用法：
    python scripts/import_opencmkg.py                 # 全量导入（先清库）
    python scripts/import_opencmkg.py --dry-run       # 只解析统计，不写库
    python scripts/import_opencmkg.py --keep-existing --batch-size 20000
    python scripts/import_opencmkg.py --sample-out data/sample_graph.csv
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "data" / "opencmkg"

DATASET = "OpenCMKG"

# README 收录的 13 种关系码 → 中文语义名（写入 display_relation_original）
RELATION_DISPLAY: Dict[str, str] = {
    "department_belong_department": "科室所属关系",
    "disease_acompany_disease": "并发症",
    "disease_belong_department": "疾病所属科室",
    "disease_common_drug": "疾病一般用药",
    "disease_eat_food": "疾病可吃食物",
    "disease_has_symptom": "疾病对应症状",
    "disease_need_check": "疾病需要检查",
    "disease_need_treatment": "疾病需要治疗",
    "disease_noteat_food": "疾病忌吃食物",
    "disease_recommand_drug": "疾病推荐药物",
    "disease_recommand_food": "疾病推荐食物",
    "drug_relate_producer": "药物生产商",
    "SameAs": "同义实体",
}
KNOWN_RELATIONS = set(RELATION_DISPLAY)

# 关系两端实体的类型推断（entities_dict 查不到时的兜底）
RELATION_TYPE_HINTS: Dict[str, Tuple[str, str]] = {
    "department_belong_department": ("department", "department"),
    "disease_acompany_disease": ("disease", "disease"),
    "disease_belong_department": ("disease", "department"),
    "disease_common_drug": ("disease", "drug"),
    "disease_eat_food": ("disease", "food"),
    "disease_has_symptom": ("disease", "symptom"),
    "disease_need_check": ("disease", "unknown"),
    "disease_need_treatment": ("disease", "treatment"),
    "disease_noteat_food": ("disease", "food"),
    "disease_recommand_drug": ("disease", "drug"),
    "disease_recommand_food": ("disease", "food"),
    "drug_relate_producer": ("drug", "producer"),
    "SameAs": ("unknown", "unknown"),
}

CREATE_CONSTRAINT = (
    "CREATE CONSTRAINT entity_key_unique IF NOT EXISTS "
    "FOR (e:Entity) REQUIRE e.entity_key IS UNIQUE"
)
CREATE_NODE_TYPE_INDEX = (
    "CREATE INDEX entity_type_index IF NOT EXISTS "
    "FOR (e:Entity) ON (e.entity_type)"
)

MERGE_ENTITIES = """
UNWIND $rows AS row
MERGE (e:Entity {entity_key: row.entity_key})
SET e.source_id = row.source_id,
    e.name_original = row.name_original,
    e.name_zh = row.name_zh,
    e.entity_type = row.entity_type,
    e.source_dataset = row.source_dataset,
    e.description = row.description
"""

MERGE_RELATIONS = """
UNWIND $rows AS row
MATCH (a:Entity {entity_key: row.src_key})
MATCH (b:Entity {entity_key: row.dst_key})
CREATE (a)-[r:RELATED_TO {relation_code: row.relation_code}]->(b)
SET r.display_relation_original = row.relation_display,
    r.source_dataset = row.source_dataset
"""

WIPE_RELATIONS = "MATCH ()-[r:RELATED_TO]->() WITH r LIMIT $limit DELETE r"
WIPE_NODES = "MATCH (n:Entity) WITH n LIMIT $limit DELETE n"
COUNT_ENTITIES = "MATCH (n:Entity) RETURN count(n) AS nodes"
COUNT_RELATIONS = "MATCH ()-[r:RELATED_TO]->() RETURN count(r) AS rels"

# 清库分两阶段分批：一次性 DETACH DELETE 整图会撞 Neo4j 单事务内存上限（既定教训）
WIPE_REL_BATCH = 20000
WIPE_NODE_BATCH = 2000


def wipe_entities(session: Any) -> int:
    """分批清空图谱：先删关系再删节点，返回删除的节点数。"""
    while True:
        summary = session.run(WIPE_RELATIONS, limit=WIPE_REL_BATCH).consume()
        if not summary.counters.relationships_deleted:
            break
    removed = 0
    while True:
        summary = session.run(WIPE_NODES, limit=WIPE_NODE_BATCH).consume()
        deleted = summary.counters.nodes_deleted
        if not deleted:
            return removed
        removed += deleted


def chunks(items: List[Any], size: int) -> Iterator[List[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def load_type_map(dict_path: Path) -> Dict[str, str]:
    """entities_dict.txt → {实体名: 类别}。跨类别重名时先见先得并计数。

    源文件是近似 Python 字面量的 dict，但有 4 个实体名丢了引号成了裸标识符
    （neu / B100 / LPE / PAIgA），literal_eval 会炸；先把它们补回引号。
    """
    raw = dict_path.read_text(encoding="utf-8")
    # 4 个丢引号的裸标识符按字符串同名注入，literal_eval 不支持 Name 节点
    bare = {"neu": "neu", "B100": "B100", "LPE": "LPE", "PAIgA": "PAIgA", "nan": ""}
    mapping = eval(raw, {"__builtins__": {}}, bare)  # noqa: S307 - 本地一次性导入脚本
    type_map: Dict[str, str] = {}
    conflicts = 0
    for category, names in mapping.items():
        for name in names:
            name = str(name).strip()
            if not name:
                continue
            if name in type_map and type_map[name] != category:
                conflicts += 1
                continue
            type_map.setdefault(name, category)
    if conflicts:
        print(f"实体跨类别重名 {conflicts} 例（按先见类别归属）")
    return type_map


def parse_line(line: str) -> Optional[Tuple[str, str, str]]:
    """解析一行三元组 → (实体1, 关系码, 实体2)；无法识别关系码返回 None。"""
    parts = line.rstrip("\n").split(",")
    if len(parts) < 3:
        return None
    for i in range(1, len(parts) - 1):
        if parts[i] in KNOWN_RELATIONS:
            src = ",".join(parts[:i]).strip()
            dst = ",".join(parts[i + 1 :]).strip()
            if src and dst:
                return src, parts[i], dst
            return None
    return None


def entity_key(name: str, entity_type: str) -> str:
    return f"CMKG:{entity_type}:{name}"


def build_entities(
    triples_path: Path, type_map: Dict[str, str]
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]], Counter]:
    """两遍扫描里的第一遍：确定全部实体及类型；同时收集关系行（实体名→键稍后替换）。

    返回 (实体表 {entity_key: 行}, 关系行 [{src,dst,rel}], 统计计数)。
    """
    entities: Dict[str, Dict[str, Any]] = {}
    relations: List[Dict[str, Any]] = []
    stats: Counter = Counter()

    def ensure_entity(name: str, hint: str) -> str:
        etype = type_map.get(name) or hint or "unknown"
        key = entity_key(name, etype)
        if key not in entities:
            entities[key] = {
                "entity_key": key,
                "source_id": name,
                "name_original": name,
                "name_zh": name,
                "entity_type": etype,
                "source_dataset": DATASET,
                "description": "",
            }
        return key

    with triples_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            parsed = parse_line(line)
            if parsed is None:
                stats["unparsed"] += 1
                continue
            src_name, rel, dst_name = parsed
            src_hint, dst_hint = RELATION_TYPE_HINTS.get(rel, ("unknown", "unknown"))
            src_key = ensure_entity(src_name, src_hint)
            dst_key = ensure_entity(dst_name, dst_hint)
            relations.append(
                {
                    "src_key": src_key,
                    "dst_key": dst_key,
                    "relation_code": rel,
                    "relation_display": RELATION_DISPLAY[rel],
                    "source_dataset": DATASET,
                }
            )
            stats[rel] += 1
    return entities, relations, stats


# --------------------------------------------------------------------------- #
# 样例导出（GRAPH_BACKEND=sample 降级路径用）
# --------------------------------------------------------------------------- #
SAMPLE_SEEDS = ["高血压", "糖尿病"]


def export_sample(
    entities: Dict[str, Dict[str, Any]],
    relations: List[Dict[str, Any]],
    out_path: Path,
    max_rows: int = 60,
) -> int:
    """围绕种子疾病挑一跳关系，写成 SampleGraphRepository 认识的 CSV。"""
    import csv as _csv

    picked: List[Dict[str, Any]] = []
    seen = set()
    for seed in SAMPLE_SEEDS:
        for edge in relations:
            src = entities[edge["src_key"]]
            dst = entities[edge["dst_key"]]
            if seed not in (src["name_original"], dst["name_original"]):
                continue
            row_id = (edge["src_key"], edge["relation_code"], edge["dst_key"])
            if row_id in seen:
                continue
            seen.add(row_id)
            picked.append((src, edge, dst))
            if len(picked) >= max_rows:
                break
        if len(picked) >= max_rows:
            break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = _csv.writer(fh)
        writer.writerow(
            [
                "src_key", "src_name_original", "src_name_zh", "src_type", "src_desc",
                "dst_key", "dst_name_original", "dst_name_zh", "dst_type", "dst_desc",
                "relation_code", "relation_display", "source_dataset",
            ]
        )
        for src, edge, dst in picked:
            writer.writerow(
                [
                    src["entity_key"], src["name_original"], src["name_zh"], src["entity_type"], "",
                    dst["entity_key"], dst["name_original"], dst["name_zh"], dst["entity_type"], "",
                    edge["relation_code"], edge["relation_display"], DATASET,
                ]
            )
    return len(picked)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def _load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main() -> int:
    parser = argparse.ArgumentParser(description="导入 OpenCMKG 到 Neo4j")
    parser.add_argument("--data-dir", default=str(DEFAULT_DIR), help="opencmkg 数据目录")
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument("--keep-existing", action="store_true", help="不清理已有 :Entity")
    parser.add_argument("--dry-run", action="store_true", help="只解析统计，不连库")
    parser.add_argument("--sample-out", default="", help="同时导出降级样例 CSV 到该路径")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    triples_path = data_dir / "triples.txt"
    dict_path = data_dir / "entities_dict.txt"
    for path in (triples_path, dict_path):
        if not path.exists():
            print(f"数据文件缺失: {path}（先下载 OpenCMKG）")
            return 1

    t0 = time.time()
    type_map = load_type_map(dict_path)
    print(f"实体字典载入: {len(type_map)} 实体（{time.time() - t0:.1f}s）")

    entities, relations, stats = build_entities(triples_path, type_map)
    print(
        f"解析完成（{time.time() - t0:.1f}s）：{len(entities)} 实体 / {len(relations)} 关系"
        + (f"，无法解析跳过 {stats['unparsed']} 行" if stats["unparsed"] else "")
    )
    print("关系分布:", dict(sorted(stats.items(), key=lambda kv: -kv[1]) if stats else []))

    if args.sample_out:
        n = export_sample(entities, relations, Path(args.sample_out))
        print(f"样例导出: {args.sample_out}（{n} 行）")

    if args.dry_run:
        return 0

    _load_env()
    from neo4j import GraphDatabase

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    if not password:
        print("请设置 NEO4J_PASSWORD（或在 .env 中配置）")
        return 1

    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()

    written_nodes = written_rels = 0
    with driver.session(database=database) as session:
        session.run(CREATE_CONSTRAINT)
        session.run(CREATE_NODE_TYPE_INDEX)

        if not args.keep_existing:
            before = session.run(COUNT_ENTITIES).single()["nodes"]
            if before:
                print(f"清理已有 :Entity（{before} 个；先删关系再删节点）…")
                removed = wipe_entities(session)
                print(f"已清理 {removed} 个节点")

        print("写入节点…")
        for batch in chunks(list(entities.values()), args.batch_size):
            session.run(MERGE_ENTITIES, rows=batch)
            written_nodes += len(batch)
        print(f"节点写入完成: {written_nodes}")

        print("写入关系…")
        last = time.time()
        for batch in chunks(relations, args.batch_size):
            session.run(MERGE_RELATIONS, rows=batch)
            written_rels += len(batch)
            now = time.time()
            if now - last >= 15:
                print(f"  … 已写入 {written_rels} 条关系（{now - t0:.0f}s）")
                last = now

        counts = session.run(
            "MATCH (n:Entity) WITH count(n) AS nodes "
            "MATCH ()-[r:RELATED_TO]->() RETURN nodes, count(r) AS rels"
        ).single()

    driver.close()
    print(
        f"\n导入完成（{time.time() - t0:.0f}s）\n"
        f"  本次写入: {written_nodes} 节点 / {written_rels} 关系\n"
        f"  库内合计: {counts['nodes']} 节点 / {counts['rels']} 关系"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
