"""内置长期记忆引擎装配（构建细节 §7）。

本模块只做一件事：把 openjiuwen 内置的 `LongTermMemory` 用真实存储装配起来，
再把它适配成 `app.services.memory_service.MemoryEngine` 端口。
编排（何时检索、何时沉淀、失败降级）全在 `MemoryService`，这里不掺业务。

装配方式（实测可用）：
    LongTermMemory.register_store(
        kv_store        = DbBasedKVStore(与 db_store 共用同一 SQLAlchemy 引擎),
        vector_store    = Chroma（持久化到 CHROMA_DIR）,
        db_store        = DefaultDbStore（SQLite，承载 messages/user_profile 等表）,
        embedding_model = 百炼 APIEmbedding（DeepSeek 不提供 embedding）,
    )
注意：SDK 全库没有 PostgreSQL 记忆适配器，SQLite 是唯一可直接落地的后端

隔离键是 `user_id` + `scope_id`（scope_id 全项目固定），session_id 只用于消息归档。
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Sequence

from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.foundation.store import (
    DbBasedKVStore,
    DefaultDbStore,
    create_vector_store,
)
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.memory.config.config import (
    AgentMemoryConfig,
    MemoryEngineConfig,
    MemoryScopeConfig,
)
from openjiuwen.core.memory.long_term_memory import LongTermMemory
from openjiuwen.core.retrieval.embedding.api_embedding import APIEmbedding
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.schemas import MemoryHit, Message
from app.settings import Settings
from app.throttle import RateGate, ThrottledModel

logger = logging.getLogger(__name__)


def _install_model_gate(gate: Optional[RateGate]) -> None:
    """让内置记忆引擎创建的所有 LLM 实例都带上闸门。

    SDK 的记忆引擎内部固定用 `LongTermMemory._get_llm_from_config` 造模型，
    没有注入点，只能替换这个静态方法。这是本文件唯一一处"碰 SDK 内部"的代码，
    目的就是让记忆侧请求与 Agent 侧共用同一节流（RPM 限额，见构建细节 §12）。
    """
    if gate is None or getattr(LongTermMemory, "_app_gate_installed", False):
        return

    def _factory(
        model_config: ModelRequestConfig, model_client_config: ModelClientConfig
    ) -> ThrottledModel:
        return ThrottledModel(model_client_config, model_config, gate=gate)

    LongTermMemory._get_llm_from_config = staticmethod(_factory)
    LongTermMemory._app_gate_installed = True


class OpenjiuwenMemoryEngine:
    """把 `LongTermMemory` 适配成 `MemoryEngine` 端口。

    端口方法签名见 `app.services.memory_service.MemoryEngine`：
    `search(...)` 只消耗 embedding（不占 LLM 配额），`add(...)` 才会调 LLM 抽取。
    """

    def __init__(
        self,
        memory: LongTermMemory,
        agent_config: AgentMemoryConfig,
        sql_engine: AsyncEngine,
        *,
        gate: Optional[Any] = None,
    ) -> None:
        self._memory = memory
        self._agent_config = agent_config
        self._sql_engine = sql_engine
        # 记忆抽取也要调 LLM，必须和 Agent 共用同一节流闸门（RPM 限额）
        self._gate = gate

    async def search(
        self, *, user_id: str, scope_id: str, query: str, num: int
    ) -> List[MemoryHit]:
        """语义/情景片段 + 摘要，合并后按分数排序取前 num 条。"""
        hits: List[MemoryHit] = []

        fragments = await self._memory.search_user_mem(
            query=query, num=num, user_id=user_id, scope_id=scope_id
        )
        for item in fragments or []:
            info = item.mem_info
            hits.append(
                MemoryHit(
                    memory_id=info.mem_id or "",
                    kind=_type_name(info.type),
                    content=info.content or "",
                    score=float(item.score or 0.0),
                )
            )

        summaries = await self._memory.search_user_history_summary(
            query=query, num=num, user_id=user_id, scope_id=scope_id
        )
        for item in summaries or []:
            info = item.mem_info
            hits.append(
                MemoryHit(
                    memory_id=info.mem_id or "",
                    kind="summary",
                    content=info.content or "",
                    score=float(item.score or 0.0),
                )
            )

        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:num]

    async def add(
        self,
        *,
        user_id: str,
        scope_id: str,
        session_id: str,
        messages: Sequence[Message],
    ) -> bool:
        """把一轮对话沉淀成长期记忆。引擎内部会再调一次 LLM 做记忆抽取。"""
        converted: List[Any] = []
        for msg in messages:
            if msg.role == "user":
                converted.append(UserMessage(content=msg.content))
            elif msg.role == "assistant":
                converted.append(AssistantMessage(content=msg.content))
        if not converted:
            return False

        if self._gate is not None:
            await self._gate.acquire()

        await self._memory.add_messages(
            messages=converted,
            agent_config=self._agent_config,
            user_id=user_id,
            scope_id=scope_id,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc),
        )
        return True

    async def delete_user(self, *, user_id: str, scope_id: str) -> bool:
        """按 user_id 删除全部记忆（对应策划书的删除承诺）。"""
        await self._memory.delete_mem_by_user_id(user_id=user_id, scope_id=scope_id)
        return True

    async def close(self) -> None:
        """释放 SQLAlchemy 引擎连接池。"""
        await self._sql_engine.dispose()


def _type_name(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


async def build_memory_engine(
    settings: Settings, *, gate: Optional[Any] = None
) -> OpenjiuwenMemoryEngine:
    """装配内置长期记忆引擎（SQLite + Chroma + 百炼 embedding）。

    `gate` 是 LLM 节流闸门（见 `app.factory.ThrottleRail`），用于让记忆抽取
    与 Agent 推理共用同一节流，避免按 RPM 限额撞 429。
    """
    _install_model_gate(gate)

    memory = LongTermMemory()

    sql_engine = create_async_engine(
        f"sqlite+aiosqlite:///{Path(settings.sqlite_path).as_posix()}", echo=False
    )
    kv_store = DbBasedKVStore(sql_engine)
    db_store = DefaultDbStore(sql_engine)
    vector_store = create_vector_store(
        "chroma", persist_directory=settings.chroma_dir
    )
    if vector_store is None:
        raise RuntimeError("Chroma 向量库不可用，请确认已安装 chromadb。")

    embedding = APIEmbedding(
        EmbeddingConfig(
            model_name=settings.embedding_model,
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
        )
    )

    await memory.register_store(
        kv_store=kv_store,
        vector_store=vector_store,
        db_store=db_store,
        embedding_model=embedding,
    )

    model_client = ModelClientConfig(
        client_provider="openai",
        api_key=settings.api_key,
        api_base=settings.base_url,
    )
    model_cfg = ModelRequestConfig(model_name=settings.model)
    memory.set_config(
        MemoryEngineConfig(
            default_model_cfg=model_cfg, default_model_client_cfg=model_client
        )
    )
    # scope 配置必须显式写入：检索要用它取 embedding，沉淀要用它取 LLM。
    await memory.set_scope_config(
        settings.memory_scope_id,
        MemoryScopeConfig(
            model_cfg=model_cfg,
            model_client_cfg=model_client,
            embedding_cfg=EmbeddingConfig(
                model_name=settings.embedding_model,
                base_url=settings.embedding_base_url,
                api_key=settings.embedding_api_key,
            ),
        ),
    )

    agent_config = AgentMemoryConfig(
        enable_long_term_mem=True,
        enable_user_profile=settings.memory_enable_profile,
        enable_semantic_memory=settings.memory_enable_semantic,
        enable_episodic_memory=settings.memory_enable_episodic,
        enable_summary_memory=settings.memory_enable_summary,
    )

    logger.info(
        "长期记忆引擎装配完成：scope=%s profile=%s semantic=%s episodic=%s summary=%s",
        settings.memory_scope_id,
        agent_config.enable_user_profile,
        agent_config.enable_semantic_memory,
        agent_config.enable_episodic_memory,
        agent_config.enable_summary_memory,
    )
    return OpenjiuwenMemoryEngine(
        memory, agent_config, sql_engine, gate=gate
    )
