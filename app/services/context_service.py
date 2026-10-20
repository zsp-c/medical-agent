"""短期上下文服务。

职责边界（很重要，避免和 SDK 打架）：
- **会话归属守卫**：一个 session_id 只属于一个 user_id，防止跨用户串会话
- **消息归档**：完整历史写入 ConversationStore（SQLite 默认实现），随取随用
- **给长期记忆提供素材**：`load_recent` 取最近若干条，作为记忆沉淀的输入

注意：**发给模型的滑动窗口不是这里拼的**。openjiuwen 的 ContextEngine 会按
`conversation_id`（= session_id）自动维护最近 N 轮的上下文并处理消息角色，
我们再把历史文本塞进 query 只会造成重复注入。因此本服务只做"存档 + 供料"。
"""

from typing import List, Optional

from app.repositories.conversation_store import ConversationStore
from app.schemas import Message

# 一轮对话 = 1 条 user + 1 条 assistant
MESSAGES_PER_ROUND = 2


class ContextService:
    """会话归属校验 + 消息归档 + 近期消息读取。"""

    def __init__(self, store: ConversationStore, window_rounds: int = 10) -> None:
        self._store = store
        self._window_rounds = max(1, int(window_rounds))

    # ---- 归属守卫 ----
    def ensure_session(self, user_id: str, session_id: str) -> None:
        """登记并校验会话归属；不属于该 user_id 时抛 SessionOwnershipError。"""
        self._store.ensure_session(session_id, user_id)

    # ---- 读取 ----
    def load_recent(
        self, user_id: str, session_id: str, limit: Optional[int] = None
    ) -> List[Message]:
        """返回最近若干条消息（时间正序）。默认取 `CONTEXT_WINDOW_ROUNDS` 轮的条数。"""
        self.ensure_session(user_id, session_id)
        n = limit if limit is not None else self._window_rounds * MESSAGES_PER_ROUND
        return self._store.load_recent(session_id, n)

    # ---- 写入 ----
    def append_turn(
        self,
        user_id: str,
        session_id: str,
        query: str,
        answer: str,
        request_id: str = "",
    ) -> int:
        """写回一轮对话（用户消息 + 助手回答）。request_id 相同则幂等跳过。"""
        items = [
            Message(
                session_id=session_id,
                user_id=user_id,
                role="user",
                content=query,
                request_id=request_id,
            ),
            Message(
                session_id=session_id,
                user_id=user_id,
                role="assistant",
                content=answer,
                request_id=request_id,
            ),
        ]
        return self._store.append(items)

    # ---- 调试 ----
    def count(self, session_id: str) -> int:
        counter = getattr(self._store, "count_messages", None)
        return counter(session_id) if callable(counter) else 0
