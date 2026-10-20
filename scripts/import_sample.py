"""把小样例图谱导入 Neo4j，用于打通「工具 → Cypher → 图谱」链路。

用法（先确保容器已起）：
    docker run -d --name oj-neo4j -p 7474:7474 -p 7687:7687 \
        -e NEO4J_AUTH=neo4j/<你的密码> neo4j:5.26-community
    python scripts/import_sample.py

连接信息按优先级从环境变量读取，缺省与 .env.example 一致。
"""

import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "data" / "sample_graph.csv"

CREATE_CONSTRAINT = (
    "CREATE CONSTRAINT entity_key_unique IF NOT EXISTS "
    "FOR (e:Entity) REQUIRE e.entity_key IS UNIQUE"
)

MERGE_ENTITY = """
UNWIND $rows AS row
MERGE (e:Entity {entity_key: row.entity_key})
SET e.source_id = row.entity_key,
    e.name_original = row.name_original,
    e.name_zh = row.name_zh,
    e.entity_type = row.entity_type,
    e.description = row.description,
    e.source_dataset = row.source_dataset
"""

MERGE_RELATION = """
UNWIND $rows AS row
MATCH (a:Entity {entity_key: row.src_key})
MATCH (b:Entity {entity_key: row.dst_key})
MERGE (a)-[r:RELATED_TO {relation_code: row.relation_code}]->(b)
SET r.display_relation_original = row.relation_display,
    r.source_dataset = row.source_dataset
"""


def load_csv(csv_path: Path):
    entities, relations = {}, []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            dataset = row.get("source_dataset", "SampleKG")
            for side in ("src", "dst"):
                key = row[f"{side}_key"]
                entities.setdefault(
                    key,
                    {
                        "entity_key": key,
                        "name_original": row[f"{side}_name_original"],
                        "name_zh": row[f"{side}_name_zh"],
                        "entity_type": row[f"{side}_type"],
                        "description": row[f"{side}_desc"],
                        "source_dataset": dataset,
                    },
                )
            relations.append(
                {
                    "src_key": row["src_key"],
                    "dst_key": row["dst_key"],
                    "relation_code": row["relation_code"],
                    "relation_display": row["relation_display"],
                    "source_dataset": dataset,
                }
            )
    return list(entities.values()), relations


def main() -> int:
    csv_path = Path(os.environ.get("SAMPLE_GRAPH") or DEFAULT_CSV)
    if not csv_path.exists():
        print(f"样例数据不存在: {csv_path}")
        return 1

    from neo4j import GraphDatabase

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    if not password:
        print("请设置 NEO4J_PASSWORD（或在 .env 中配置）")
        return 1

    entities, relations = load_csv(csv_path)
    print(f"读取 {csv_path.name}: {len(entities)} 个实体, {len(relations)} 条关系")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    with driver.session(database=database) as session:
        session.run(CREATE_CONSTRAINT)
        session.run(MERGE_ENTITY, rows=entities)
        session.run(MERGE_RELATION, rows=relations)
        counts = session.run(
            "MATCH (e:Entity) WITH count(e) AS nodes "
            "MATCH ()-[r:RELATED_TO]->() RETURN nodes, count(r) AS rels"
        ).single()
    driver.close()

    print(f"导入完成: {counts['nodes']} 个节点, {counts['rels']} 条关系")
    print("浏览器可视化: http://localhost:7474  （用户名 neo4j）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
