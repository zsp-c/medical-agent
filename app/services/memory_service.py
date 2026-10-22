"""长期记忆服务（编排 + 降级）。

记忆引擎本身在 `app/memory/setup.py` 装配（内置 openjiuwen 长期记忆引擎），
本模块只负责**编排**与**降级**，不关心存储细节。

为什么必须显式编排：openjiuwen 的 `ReActAgentConfig` 没有任何记忆字段，
记忆不会自动挂载。每轮的"先检索、后推理、最后沉淀"必须由这里驱动。

设计约束：
- 检索在调用 LLM **之前**，沉淀在回复**之后**
- 任一环节失败 → 记日志 + 返回空结果/False，**绝不阻断对话**
- 引擎未装配（`engine=None`）时整体降级为无记忆
"""

import asyncio
import logging
from typing import List, Optional, Protocol, Sequence

from app.schemas import MemoryHit, Message

logger = logging.getLogger(__name__)


class MemoryEngine(Protocol):
    """记忆引擎端口。`app/memory/setup.py` 提供基于内置引擎的实现。"""

    async def search(
        self, *, user_id: str, scope_id: str, query: str, num: int
    ) -> List[MemoryHit]:
        """语义/摘要召回。"""

    async def add(
        self,
        *,
        user_id: str,
        scope_id: str,
        session_id: str,
        messages: Sequence[Message],
    ) -> bool:
        """把一轮对话沉淀成长期记忆。"""


class MemoryService:
    """长期记忆的检索/沉淀编排。engine 为空即降级为无记忆。"""

    def __init__(
        self,
        scope_id: str,
        engine: Optional[MemoryEngine] = None,
        num: int = 10,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._scope_id = scope_id
        self._engine = engine
        self._num = num
        # 检索是本轮的固定前置成本：嵌入接口慢时宁可本轮无记忆，也不把用户堵在这里
        self._timeout = timeout_seconds

    @property
    def enabled(self) -> bool:
        return self._engine is not None

    async def retrieve(self, user_id: str, query: str) -> List[MemoryHit]:
        """检索与当前问题相关的长期记忆；失败或超时返回空列表。"""
        if self._engine is None or not user_id:
            return []
        try:
            return await asyncio.wait_for(
                self._engine.search(
                    user_id=user_id, scope_id=self._scope_id, query=query, num=self._num
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("长期记忆检索超时（>%.1fs），本轮降级为无记忆", self._timeout)
            return []
        except Exception:
            logger.warning("长期记忆检索失败，本轮降级为无记忆", exc_info=True)
            return []

    async def remember(
        self,
        user_id: str,
        session_id: str,
        messages: Sequence[Message],
    ) -> bool:
        """沉淀长期记忆；失败返回 False，**不回滚已写入的会话消息**。"""
        if self._engine is None or not user_id or not messages:
            return False
        try:
            return await self._engine.add(
                user_id=user_id,
                scope_id=self._scope_id,
                session_id=session_id,
                messages=messages,
            )
        except Exception:
            logger.warning("长期记忆沉淀失败，已忽略", exc_info=True)
            return False

    async def forget(self, user_id: str) -> bool:
        """按 user_id 删除记忆（对应策划书的删除承诺）。引擎不支持时返回 False。"""
        if self._engine is None:
            return False
        cleaner = getattr(self._engine, "delete_user", None)
        if not callable(cleaner):
            return False
        try:
            return bool(await cleaner(user_id=user_id, scope_id=self._scope_id))
        except Exception:
            logger.warning("长期记忆删除失败", exc_info=True)
            return False

    async def close(self) -> None:
        """释放记忆引擎资源（未装配时是空操作）。"""
        if self._engine is None:
            return
        closer = getattr(self._engine, "close", None)
        if callable(closer):
            await closer()
