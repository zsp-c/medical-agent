"""中文医疗知识图谱 Agent（Agent 层）。

对外入口：
- 契约层 `app.schemas`、配置层 `app.settings`
- 业务层 `app.services`（后端只调 `AgentService.handle_chat`）
- 装配层 `app.factory`（`build_agent` / `close`）
"""

from app.factory import AgentPool, BuiltAgent, build_agent, close
from app.schemas import (
    ChatRequest,
    ChatResponse,
    EntityBrief,
    EvidenceRef,
    GraphResult,
    LLMConfig,
    MemoryHit,
    Message,
    RelationEdge,
)
from app.services import SessionOwnershipError
from app.services.agent_service import AgentService
from app.settings import Settings

__all__ = [
    "Settings",
    "build_agent",
    "close",
    "AgentPool",
    "BuiltAgent",
    "AgentService",
    "SessionOwnershipError",
    "ChatRequest",
    "ChatResponse",
    "EntityBrief",
    "EvidenceRef",
    "GraphResult",
    "LLMConfig",
    "MemoryHit",
    "Message",
    "RelationEdge",
]
