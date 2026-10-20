"""图谱工具（白名单）。

模型只能调用这里注册的函数；Cypher 由 `GraphQueryService` 用参数化模板生成，
模型无法自行拼写查询语句。

返回值统一是 JSON 字符串（工具结果需要是文本），字段固定，便于：
- 模型据此组织带证据的回答
- 前端直接喂给 Cytoscape.js
"""

import json
from typing import Any, List

from openjiuwen.core.foundation.tool import LocalFunction, tool

from app.services.graph_query_service import (
    PATH_MAX_HOPS,
    RELATION_LIMIT_MAX,
    SEARCH_LIMIT_MAX,
    SUBGRAPH_DEPTH_MAX,
    SUBGRAPH_NODES_MAX,
    GraphQueryService,
)


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _graph_payload(result: Any, source: str) -> dict:
    payload = {
        "source": source,
        "backend": result.meta.get("backend", ""),
        "truncated": result.truncated,
        "nodes": [n.model_dump() for n in result.nodes],
        "edges": [e.model_dump() for e in result.edges],
    }
    if result.meta.get("relation_counts"):
        payload["relation_counts"] = result.meta["relation_counts"]
    return payload


def make_graph_tools(service: GraphQueryService) -> List[LocalFunction]:
    """把 5 个白名单能力包装成模型可调用的工具。"""

    async def search_entity(
        query: str, entity_type: str = "", limit: int = 10
    ) -> str:
        """在医疗知识图谱中按名称模糊搜索实体，返回候选实体的 entity_key。

        任何涉及具体疾病/症状/药物/基因/蛋白的问题，都应先用本工具拿到 entity_key，
        再用其他工具查细节或关系。不要在没拿到 entity_key 前猜测或编造实体。

        Args:
            query: 搜索关键词，按英文名（如“Hypertension”）或 entity_key 匹配。
                中文词请先自行译成标准英文医学术语再传入（你的翻译不消耗工具调用）。
            entity_type: 可选，限定实体类型：disease / symptom / drug / drug_class / gene / protein / anatomy / phenotype。
            limit: 返回条数，默认 10，最大 20。

        Returns:
            JSON 字符串：nodes 为候选实体列表（含 entity_key / name / name_zh / entity_type），
            truncated 表示是否被截断。nodes 为空说明图谱中未检索到。
        """
        result = service.search_entity(query, entity_type or None, limit)
        payload = _graph_payload(result, "search_entity")
        if not result.nodes:
            payload["hint"] = (
                "图谱中未检索到该实体。复合概念请拆成更短的标准术语分别搜索"
                "（例如把“肾素-血管紧张素系统”拆成“肾素”“血管紧张素”），"
                "不要用同一个词反复重试；确实查不到就如实告知用户。"
            )
        return _dumps(payload)

    async def get_entity_details(entity_key: str) -> str:
        """查询单个实体的详细属性（类型、中英文名、描述、邻居数量）。

        Args:
            entity_key: 实体唯一键，必须来自 search_entity 的返回结果。

        Returns:
            JSON 字符串：found=false 表示该 entity_key 不存在。
        """
        return _dumps(service.get_entity_details(entity_key))

    async def query_relations(
        entity_key: str,
        relation_code: str = "",
        direction: str = "both",
        limit: int = 20,
    ) -> str:
        """查询某个实体的一跳关系（邻居实体与关系类型）。

        Args:
            entity_key: 实体唯一键，必须来自 search_entity 的返回结果。
            relation_code: 可选，按关系类型过滤（留空返回全部）。实际取值：
                indication / contraindication / off_label_use / side_effect /
                target / enzyme / transporter / carrier / synergistic_interaction /
                associated_with / phenotype_present / phenotype_absent / linked_to /
                parent_child / expression_present / expression_absent /
                interacts_with / ppi。
            direction: out=该实体作为起点的关系，in=作为终点的关系，both=两者都要（默认）。
                注意图谱每条关系正反各存一行，方向不表示主体与客体。
            limit: 返回条数，默认 20，最大 50。

        Returns:
            JSON 字符串：edges 为关系列表（src_key / relation_code / dst_key / dst_name /
            src_source / dst_source），nodes 为对端实体列表；relation_counts 为该实体
            各关系类型的总条数（不受 limit 限制），度数大的实体应先用它看清有哪些关系类型，
            再用 relation_code 精确查询。truncated 为 true 时 edges 不是全集。

        回答时注意：
        - parent_child 只是本体分类层级，不代表临床相关性；问“相关疾病/并发症/风险”时不能当依据。
        - associated_with / interacts_with / expression_present 等只表示存在关联记录，
          不等于因果或临床疗效，不要表述成“导致”“可治疗”。
        """
        result = service.query_relations(entity_key, relation_code or None, direction, limit)
        return _dumps(_graph_payload(result, "query_relations"))

    async def find_relation_path(
        src_key: str, dst_key: str, max_hops: int = 2
    ) -> str:
        """查询两个实体之间的最短关系路径（无向多跳，最多 3 跳）。

        适合回答“A 和 B 是怎么关联的”“通路是什么”这类问题。

        Args:
            src_key: 起点实体的 entity_key。
            dst_key: 终点实体的 entity_key。
            max_hops: 最大跳数，默认 2，最大 3。

        Returns:
            JSON 字符串：found=false 表示该跳数内不存在通路。

        回答时注意：路径只表示两个实体“可达”，不等于药效或因果链；
        不要因为有通路就下“A 能治疗 B”这类结论。只有 relation_code 为 indication
        的关系才带临床用途含义，且需点明是图谱记录。
        """
        return _dumps(service.find_relation_path(src_key, dst_key, max_hops))

    async def get_subgraph(entity_key: str, depth: int = 1, limit: int = 50) -> str:
        """查询某个实体周围的多跳子图（节点 + 子图内部的关系）。

        适合回答“围绕 X 有哪些相关实体”“给我一张局部图谱”这类问题。

        Args:
            entity_key: 中心实体唯一键，必须来自 search_entity 的返回结果。
            depth: 跳数，1 或 2，默认 1。
            limit: 返回节点总数上限，默认 50，最大 50。

        Returns:
            JSON 字符串：nodes 与 edges 构成可直接渲染的子图。
        """
        result = service.get_subgraph(entity_key, depth, limit)
        return _dumps(_graph_payload(result, "get_subgraph"))

    return [
        tool(search_entity, name="search_entity"),
        tool(get_entity_details, name="get_entity_details"),
        tool(query_relations, name="query_relations"),
        tool(find_relation_path, name="find_relation_path"),
        tool(get_subgraph, name="get_subgraph"),
    ]


__all__ = [
    "make_graph_tools",
    "SEARCH_LIMIT_MAX",
    "RELATION_LIMIT_MAX",
    "PATH_MAX_HOPS",
    "SUBGRAPH_DEPTH_MAX",
    "SUBGRAPH_NODES_MAX",
]
