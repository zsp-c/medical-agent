"""图谱工具（白名单）。

模型只能调用这里注册的函数；Cypher 由 `GraphQueryService` 用参数化模板生成，
模型无法自行拼写查询语句。

返回值统一是 JSON 字符串（工具结果需要是文本），字段固定，便于：
- 模型据此组织带证据的回答
- 前端直接渲染子图
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


# 未过滤 query_relations 只返回这么多条样例边：度数大的实体（如高血压有数百条关系）
# 全量返回会把几十 KB 塞进上下文，且残留在后续每一轮的对话窗口里。
_UNFILTERED_EDGE_SAMPLE = 10

# 单轮图谱检索的硬上限：模型偶尔会反复试探不存在的路径，
# 放开会导致 ReAct 迭代打满后直接返回错误提示。
_ROUND_TOOL_BUDGET = 8


def _slim_edge(edge: Any) -> dict:
    """把一条边压缩成模型组织回答所需的最小字段。

    完整 GraphResult（含 src_source / dst_source / direction / relation_display 等）
    仍由服务层提供给 HTTP 接口与证据收集；模型侧不需要这些字段。
    """
    return {
        "relation": edge.relation_code,
        "src": edge.src_name,
        "dst": edge.dst_name,
        "dst_key": edge.dst_key,
        "dst_type": edge.dst_type,
    }


def _slim_node(node: Any) -> dict:
    """节点只留定位所需的三个字段（去掉 name_zh / source_id，模型用不到）。"""
    return {
        "entity_key": node.entity_key,
        "name": node.name,
        "entity_type": node.entity_type,
    }


def _graph_payload(result: Any, source: str) -> dict:
    # 图谱每条关系正反各存一行，方向无语义：模型侧按 (关系, 两端实体) 折叠成一条
    seen = set()
    edges = []
    for e in result.edges:
        key = (e.relation_code, frozenset((e.src_name, e.dst_name)))
        if key in seen:
            continue
        seen.add(key)
        edges.append(_slim_edge(e))
    payload = {
        "source": source,
        "truncated": result.truncated,
        "nodes": [_slim_node(n) for n in result.nodes],
        "edges": edges,
    }
    if result.meta.get("relation_counts"):
        payload["relation_counts"] = result.meta["relation_counts"]
    return payload


def _relations_payload(result: Any, *, unfiltered: bool) -> dict:
    """query_relations 的模型侧载荷。

    - 边一律走 _slim_edge 瘦身（丢弃来源、方向等字段——方向本来就无语义）；
    - 节点数组与边的 dst 字段重复，不下发（证据实体已由服务层另行收集）；
    - 未过滤调用（relation_code 留空）的目的是看 relation_counts 分布，
      此时只附前 _UNFILTERED_EDGE_SAMPLE 条样例边，避免大度实体撑爆上下文。
    """
    edges = result.edges
    sampled = False
    if unfiltered and len(edges) > _UNFILTERED_EDGE_SAMPLE:
        edges = edges[:_UNFILTERED_EDGE_SAMPLE]
        sampled = True
    payload = {
        "source": "query_relations",
        "truncated": result.truncated or sampled,
        "relation_counts": result.meta.get("relation_counts", {}),
        "edges": [_slim_edge(e) for e in edges],
    }
    if sampled:
        payload["hint"] = (
            "未过滤调用只返回关系类型分布与前 "
            f"{_UNFILTERED_EDGE_SAMPLE} 条样例；需要某类关系的完整列表时，"
            "请用 relation_code 精确查询。"
        )
    return payload


def make_graph_tools(service: GraphQueryService) -> List[LocalFunction]:
    """把 5 个白名单能力包装成模型可调用的工具。

    同轮去重：模型常对同一实体反复发完全相同的检索（拿不到结果就重试），
    既白耗图查询也白耗一轮模型调用。这里按轮次缓存已执行过的参数组合，
    重复调用直接回一句"不要重复查询"，让模型立刻转向别的信息或直接作答。
    """
    cache: dict = {"round_id": -1, "keys": set(), "count": 0}

    def _is_duplicate(args_key: tuple) -> bool:
        if cache["round_id"] != service.round_id:
            cache["round_id"] = service.round_id
            cache["keys"] = set()
            cache["count"] = 0
        if args_key in cache["keys"]:
            return True
        cache["keys"].add(args_key)
        cache["count"] += 1
        return False

    def _budget_exhausted() -> bool:
        """硬上限：超出后不再查库，逼模型用已有信息作答，避免迭代打满。"""
        return cache["count"] > _ROUND_TOOL_BUDGET

    def _duplicate_reply() -> str:
        return _dumps(
            {
                "duplicate": True,
                "hint": "本次调用的参数与本轮之前完全相同，结果已给出，不再重复执行。"
                "请基于已有结果作答，或改用不同的实体/关系类型查询。",
            }
        )

    def _budget_reply() -> str:
        return _dumps(
            {
                "budget_exhausted": True,
                "hint": f"本轮图谱检索已达 {_ROUND_TOOL_BUDGET} 次上限，不再执行新的查询。"
                "请立即用已获得的信息组织回答，并如实说明缺少的部分。",
            }
        )

    async def search_entity(
        query: str, entity_type: str = "", limit: int = 10
    ) -> str:
        """在医疗知识图谱中按名称模糊搜索实体，返回候选实体的 entity_key。

        任何涉及具体疾病/症状/药物/食物/科室/检查治疗的问题，都应先用本工具拿到
        entity_key，再用其他工具查细节或关系。不要在没拿到 entity_key 前猜测或编造实体。

        Args:
            query: 搜索关键词，直接用中文标准医学名词（如“高血压”“阿司匹林”）或 entity_key。
                本图谱为全中文图谱，不要把中文译成英文再搜。复合概念请拆成更短的名词。
            entity_type: 可选，限定实体类型：disease / symptom / drug / food /
                department / producer / treatment。
            limit: 返回条数，默认 10，最大 20。

        Returns:
            JSON 字符串：nodes 为候选实体列表（含 entity_key / name / entity_type），
            entity_key 格式为 CMKG:<类型>:<中文名>（如 CMKG:disease:高血压），
            后续所有图谱工具都以它定位实体。truncated 表示是否被截断。
            nodes 为空说明图谱中未检索到。
        """
        if _is_duplicate(("search_entity", query, entity_type, limit)):
            return _duplicate_reply()
        if _budget_exhausted():
            return _budget_reply()
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
            relation_code: 可选，按关系类型过滤（留空只看类型分布与样例）。实际取值：
                disease_has_symptom(疾病的症状表现) /
                disease_acompany_disease(并发症/伴随疾病) /
                disease_need_check(疾病需要做的检查) /
                disease_need_treatment(疾病需要的治疗手段) /
                disease_belong_department(疾病所属科室) /
                disease_recommand_drug(疾病推荐药物) /
                disease_common_drug(疾病一般用药) /
                disease_recommand_food(疾病推荐食物) /
                disease_eat_food(疾病可吃食物) /
                disease_noteat_food(疾病忌吃食物) /
                drug_relate_producer(药物生产商) /
                department_belong_department(科室层级) /
                SameAs(同义实体链接)。
            direction: out=该实体作为起点的关系，in=作为终点的关系，both=两者都要（默认）。
                注意图谱每条关系正反各存一行，方向不表示主体与客体。
            limit: 返回条数，默认 20，最大 50。

        Returns:
            JSON 字符串：relation_counts 为该实体各关系类型的总条数（不受 limit 限制）；
            relation_code 留空时 edges 只含前 10 条样例（truncated=true），
            需要某类关系的完整列表请用 relation_code 精确查询；
            过滤调用时 edges 为该类型的关系列表（relation/src/dst/dst_key/dst_type）。
            truncated 为 true 时 edges 不是全集。

        回答时注意：
        - disease_recommand_drug / disease_common_drug 等“推荐/常用”关系只是图谱中
          记录的用药信息，不等于临床处方，表述时用“图谱记录…可用于…”，不要直接
          给用户开药或替代医嘱。
        - disease_eat_food / disease_recommand_food / disease_noteat_food 是饮食建议
          记录，供参考，个体情况需遵医嘱。
        - SameAs 是同义实体链接，department_belong_department 是科室层级，都不代表
          临床因果。任何关系记录的存在都不等于已证明因果或疗效。
        """
        if _is_duplicate(("query_relations", entity_key, relation_code, direction, limit)):
            return _duplicate_reply()
        if _budget_exhausted():
            return _budget_reply()
        result = service.query_relations(entity_key, relation_code or None, direction, limit)
        payload = _relations_payload(result, unfiltered=not relation_code)
        if not result.edges:
            payload["hint"] = (
                f"该实体没有 relation_code={relation_code} 的关系记录"
                if relation_code
                else "该实体没有查到关系记录"
            ) + "。不要重复查询同一组合，也不要换 direction 再试（方向无语义）；"
            "请改用其它 relation_code 或直接基于已有信息作答。"
        return _dumps(payload)

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

        回答时注意：路径只表示两个实体“可达”，不等于因果链；
        不要因为有通路就下“A 能治疗 B”这类结论。图谱中的“推荐药物/食物”类关系
        只是记录信息，不等于临床处方，需提示用户遵医嘱。
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
        if _is_duplicate(("get_subgraph", entity_key, depth, limit)):
            return _duplicate_reply()
        if _budget_exhausted():
            return _budget_reply()
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
