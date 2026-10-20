"""对外接口契约（后端只依赖本模块 + app.services）。

这些模型刻意与「后端路由层 / 前端消费结构」对齐：字段名稳定，可直接映射成
REST 的请求体与响应体，也可直接喂给 Cytoscape.js。
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# 会话消息
# --------------------------------------------------------------------------- #
class Message(BaseModel):
    """一条归档消息。短期上下文与长期沉淀都基于它。"""

    session_id: str
    user_id: str
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    request_id: str = ""
    created_at: str = Field(default_factory=_now_iso)


# --------------------------------------------------------------------------- #
# 对话入口 / 出口
# --------------------------------------------------------------------------- #
class LLMConfig(BaseModel):
    """BYOK：用户自带的对话模型配置（OpenAI 兼容）。

    安全约束：
    - 仅存在于请求内存中，**不写** SQLite / Chroma / 日志 / meta，进程退出即消失；
    - 前端把它保存在浏览器侧（如 localStorage / 内存），每次请求随 body 传入；
    - `api_key` 不参与 repr/日志输出（`repr=False`）。
    """

    api_key: str = Field(repr=False)
    base_url: str
    model: str


class ChatRequest(BaseModel):
    """后端 → AgentService 的唯一入参。

    user_id 必须来自后端鉴权结果，**不得**由前端自行传入。
    `llm` 缺省时使用服务端 .env 里的默认模型配置（示例/自测路径）。
    """

    user_id: str
    session_id: str
    query: str
    request_id: str = ""
    llm: Optional[LLMConfig] = None
    options: Dict[str, Any] = Field(default_factory=dict)


class EntityBrief(BaseModel):
    """实体摘要。entity_key 是前端跳转图谱的唯一依据。"""

    entity_key: str
    name: str
    name_zh: str = ""
    entity_type: str = ""
    source_id: str = ""


class EvidenceRef(BaseModel):
    """证据引用，供前端跳 /api/entities/{id}。"""

    kind: Literal["entity", "relation", "path", "subgraph"]
    id: str
    label: str = ""


class MemoryHit(BaseModel):
    """一条被注入到本轮的长期记忆（可追溯）。"""

    memory_id: str
    kind: str
    content: str
    score: float = 0.0


class ChatResponse(BaseModel):
    """AgentService → 后端的唯一出参。"""

    answer: str
    request_id: str = ""
    evidence_entities: List[EntityBrief] = Field(default_factory=list)
    memory_used: List[MemoryHit] = Field(default_factory=list)
    # 工具调用轨迹、耗时、是否降级等，供前端调试面板使用
    meta: Dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 图谱工具返回结构（固定形状，便于前端直接渲染）
# --------------------------------------------------------------------------- #
class RelationEdge(BaseModel):
    src_key: str
    src_name: str = ""
    src_source: str = ""  # 源端实体的来源库（PrimeKG 的 x_source）
    relation_code: str = ""
    relation_display: str = ""
    dst_key: str
    dst_name: str = ""
    dst_type: str = ""
    dst_source: str = ""  # 目标端实体的来源库（PrimeKG 的 y_source）
    direction: Literal["out", "in"] = "out"


class GraphResult(BaseModel):
    """所有图谱工具的统一返回外壳。"""

    nodes: List[EntityBrief] = Field(default_factory=list)
    edges: List[RelationEdge] = Field(default_factory=list)
    truncated: bool = False
    meta: Dict[str, Any] = Field(default_factory=dict)
