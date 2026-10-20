"""把 PrimeKG-Plus 全量导入 Neo4j。

数据来源：Zenodo DOI 10.5281/zenodo.20796545（`zenodo_bundle.tar.gz`，解包后是 dataset/ 目录）。
文件对：`PrimeKG-Plus/primekg_plus.csv`（129,317 节点 / 7,683,206 有向边）或
`PrimeKG-Plus-RD/primekg_plus_rd.csv`（加了罕见病文献边）。

设计要点：
- **流式读取**：kg 文件近 GB 级，绝不能一次性进内存；按批 `UNWIND` 写入。
- **写入模型与 `scripts/import_sample.py` 完全一致**（`Entity` / `RELATED_TO`），
  因此 `GraphQueryService` 与 5 个图谱工具不需要改一行。
- **`relation_code` 取归一化后的 `display_relation`**（`associated with` → `associated_with`、
  `parent-child` → `parent_child`），与样例图和工具 docstring 的词表对齐；PrimeKG 原始的
  `relation` 列另存为 `relation_raw`（如 `disease_protein`），避免 `parent_child`
  这类多源关系丢失出处。
- **entity_key 用 `source:id`**（如 `DRUGBANK:DB00394`），而不是 kg 文件里的节点序号，
  这样前端 `/api/entities/{id}` 才有意义；序号仅作为文件内连接键。
- 默认先清空已有 `:Entity`，保证可重复执行不会把关系堆成两倍。

用法：
    python scripts/import_primekg.py                  # 默认 primekg_plus.csv
    python scripts/import_primekg.py --variant rd     # primekg_plus_rd.csv
    python scripts/import_primekg.py --dry-run        # 只解析统计，不写库
    python scripts/import_primekg.py --batch-size 20000 --keep-existing
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BUNDLE = ROOT / "data" / "primekg"

KG_FILENAMES = {"plus": "primekg_plus.csv", "rd": "primekg_plus_rd.csv"}


def normalize_relation(value: str) -> str:
    """把 PrimeKG 的 display_relation 归一成 relation_code。

    样例图（data/sample_graph.csv）与图谱工具 docstring 用的是归一化后的
    「语义关系名」（associated_with / increases_risk_of / ...），例如
    "associated with" -> associated_with、"parent-child" -> parent_child、
    "off-label use" -> off_label_use。若直接写入 PrimeKG 原始的 relation 列
    （disease_protein / anatomy_protein_present 等），模型按 docstring 词表
    过滤会静默返回 0 条。
    """
    return re.sub(r"[\s\-]+", "_", value.strip().lower())

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
    r.relation_raw = row.relation_raw,
    r.source_dataset = row.source_dataset
"""

WIPE_RELATIONS = "MATCH ()-[r:RELATED_TO]->() WITH r LIMIT $limit DELETE r"
WIPE_NODES = "MATCH (n:Entity) WITH n LIMIT $limit DELETE n"

COUNT_ENTITIES = "MATCH (n:Entity) RETURN count(n) AS nodes"
COUNT_RELATIONS = "MATCH ()-[r:RELATED_TO]->() RETURN count(r) AS rels"

# 清库必须分两阶段且分批：一次性 DETACH DELETE 整图会撞 Neo4j 单事务内存上限
# （实测 1.4 GiB，报 MemoryPoolOutOfMemoryError 并整体回滚）；只按节点数分批也不行，
# 因为 DETACH DELETE 会把该批节点的高阶邻居关系（枢纽可达数千条）拉进同一事务。
# 先删关系（纯关系事务很轻）、再删已无关系的节点，每批规模才可控。
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


# --------------------------------------------------------------------------- #
# 文件定位
# --------------------------------------------------------------------------- #
def find_file(bundle: Path, name: str) -> Path:
    """在解包目录里按文件名找（兼容多一层目录的情况）。"""
    files = [p for p in bundle.rglob("*") if p.is_file()]
    hits = [p for p in files if p.name == name]
    if not hits:
        # 兼容带日期前缀的变体名，例如 20260529-primekg_plus_rd.csv
        hits = [p for p in files if p.name.endswith(name)]
    if not hits:
        raise FileNotFoundError(f"在 {bundle} 下找不到 {name}")
    # 优先路径最短的那个
    return sorted(hits, key=lambda p: len(p.parts))[0]


def chunks(items: Iterable[Any], size: int) -> Iterator[List[Any]]:
    batch: List[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def read_nodes(nodes_path: Path) -> Tuple[Dict[int, Dict[str, Any]], int]:
    """读节点表：`node_index -> 节点属性`。返回 (映射, entity_key 冲突数)。"""
    nodes: Dict[int, Dict[str, Any]] = {}
    collisions = 0
    with nodes_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            index = int(row["node_index"])
            node_id = (row.get("node_id") or "").strip()
            source = (row.get("node_source") or "").strip()
            key = f"{source}:{node_id}" if node_id else f"PrimeKG:{index}"
            nodes[index] = {
                "entity_key": key,
                "source_id": node_id or str(index),
                "name_original": (row.get("node_name") or "").strip(),
                "name_zh": "",  # PrimeKG 无中文名，留空由按需翻译补齐
                "entity_type": (row.get("node_type") or "").strip(),
                "source_dataset": source,
                "description": "",
            }
    # 同一 source:id 出现多次时（PrimeKG-Plus 实测 110 例，都是基因别名重复节点，
    # 如 NCBI:406 同时叫 BMAL1 / ARNTL），只给冲突的那些加节点序号后缀，
    # 其余保持干净的 `source:id`；这样库内节点数仍与官方 129,317 一致。
    counts = Counter(node["entity_key"] for node in nodes.values())
    duplicates = [key for key, n in counts.items() if n > 1]
    for index, node in nodes.items():
        if counts[node["entity_key"]] > 1:
            node["entity_key"] = f"{node['entity_key']}:{index}"
    return nodes, len(duplicates)


def iter_relations(
    kg_path: Path, node_keys: Dict[int, str]
) -> Iterator[Tuple[Dict[str, Any], bool]]:
    """流式产出 (关系行, 端点是否存在)。"""
    with kg_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            src = node_keys.get(int(row["x_index"]))
            dst = node_keys.get(int(row["y_index"]))
            edge = {
                "src_key": src or "",
                "dst_key": dst or "",
                "relation_code": normalize_relation(
                    row.get("display_relation") or row.get("relation") or ""
                ),
                "relation_display": (row.get("display_relation") or "").strip(),
                "relation_raw": (row.get("relation") or "").strip(),
                "source_dataset": (row.get("x_source") or "").strip(),
            }
            yield edge, bool(src and dst)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="导入 PrimeKG-Plus 到 Neo4j")
    parser.add_argument("--bundle", default=str(DEFAULT_BUNDLE), help="解包后的数据目录")
    parser.add_argument("--variant", choices=["plus", "rd"], default="plus")
    # 容器已重建为 heap 2G / pagecache 2G，单事务总上限 1.4GiB（实测 dbms.memory.transaction.total.max），
    # 批量取 10000 是「批数不至于太多」与「单批内存可控」之间的折中。
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument("--keep-existing", action="store_true", help="不清理已有 :Entity")
    parser.add_argument("--dry-run", action="store_true", help="只解析统计，不连库")
    args = parser.parse_args()

    bundle = Path(args.bundle)
    kg_path = find_file(bundle, KG_FILENAMES[args.variant])
    nodes_path = find_file(bundle, "nodes.csv" if args.variant == "plus" else "primekg_plus_rd_nodes.csv")

    print(f"节点表: {nodes_path}")
    print(f"关系表: {kg_path}")
    t0 = time.time()
    nodes, duplicates = read_nodes(nodes_path)
    node_keys = {i: n["entity_key"] for i, n in nodes.items()}
    print(
        f"读取节点 {len(nodes)} 个（{time.time() - t0:.1f}s）"
        + (f"，其中 {duplicates} 个 source:id 重复键已加序号去重" if duplicates else "")
    )

    if args.dry_run:
        rels = skipped = 0
        types: Dict[str, int] = {}
        for edge, ok in iter_relations(kg_path, node_keys):
            if ok:
                rels += 1
                types[edge["relation_code"]] = types.get(edge["relation_code"], 0) + 1
            else:
                skipped += 1
        print(f"[dry-run] 可写入关系 {rels} 条，端点缺失跳过 {skipped} 条")
        print(f"[dry-run] 关系类型 {len(types)} 种: {sorted(types.items(), key=lambda x: -x[1])[:10]}")
        print(f"[dry-run] 耗时 {time.time() - t0:.1f}s")
        return 0

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

    written_nodes = written_rels = skipped_rels = 0
    with driver.session(database=database) as session:
        session.run(CREATE_CONSTRAINT)
        session.run(CREATE_NODE_TYPE_INDEX)

        if not args.keep_existing:
            before = session.run(COUNT_ENTITIES).single()["nodes"]
            if before:
                print(f"清理已有 :Entity（{before} 个；先删关系 {WIPE_REL_BATCH}/批，再删节点 {WIPE_NODE_BATCH}/批）…")
                removed = wipe_entities(session)
                print(f"已清理 {removed} 个节点")
            print(f"清理后节点数: {session.run(COUNT_ENTITIES).single()['nodes']}")

        print("写入节点…")
        for batch in chunks(nodes.values(), args.batch_size):
            session.run(MERGE_ENTITIES, rows=batch)
            written_nodes += len(batch)
        print(f"节点写入完成: {written_nodes}")

        print("写入关系…")
        batch: List[Dict[str, Any]] = []
        last = time.time()
        for edge, ok in iter_relations(kg_path, node_keys):
            if not ok:
                skipped_rels += 1
                continue
            batch.append(edge)
            if len(batch) >= args.batch_size:
                session.run(MERGE_RELATIONS, rows=batch)
                written_rels += len(batch)
                batch = []
                now = time.time()
                if now - last >= 15:
                    print(f"  … 已写入 {written_rels} 条关系（{now - t0:.0f}s）")
                    last = now
        if batch:
            session.run(MERGE_RELATIONS, rows=batch)
            written_rels += len(batch)

        counts = session.run(
            "MATCH (n:Entity) WITH count(n) AS nodes "
            "MATCH ()-[r:RELATED_TO]->() RETURN nodes, count(r) AS rels"
        ).single()

    driver.close()
    print(
        f"\n导入完成（{time.time() - t0:.0f}s）\n"
        f"  本次写入: {written_nodes} 节点 / {written_rels} 关系（端点缺失跳过 {skipped_rels}）\n"
        f"  库内合计: {counts['nodes']} 节点 / {counts['rels']} 关系"
    )
    print("浏览器可视化: http://localhost:7474 （用户名 neo4j）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
