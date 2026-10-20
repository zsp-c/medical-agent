"""会话与消息归档存储。

抽象出 `ConversationStore` 接口，默认给 SQLite 实现；将来后端要换 PostgreSQL，
只要把连接串换成 `postgresql+psycopg://...` 即可，**接口与调用方都不变**。

设计要点：
- `sessions` 记录 session 归属，用于防跨用户串会话
- `messages` 存全部历史（短期上下文只从里面取最近 N 轮，不裁剪历史）
- `request_id` 用于幂等：同一 request_id 重复提交不会写入两次
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    func,
    insert,
    select,
)
from sqlalchemy.engine import Engine

from app.schemas import Message

metadata = MetaData()

sessions = Table(
    "sessions",
    metadata,
    Column("session_id", String(64), primary_key=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)

messages = Table(
    "messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(64), nullable=False, index=True),
    Column("user_id", String(128), nullable=False),
    Column("role", String(16), nullable=False),
    Column("content", String, nullable=False),
    Column("request_id", String(64), nullable=False, default="", index=True),
    Column("created_at", String(40), nullable=False),
)


class SessionOwnershipError(RuntimeError):
    """session_id 已属于其他 user_id —— 拒绝访问，防止串会话。"""


class ConversationStore(ABC):
    """会话归档存储接口。"""

    @abstractmethod
    def init_schema(self) -> None:
        """建表（幂等）。"""

    @abstractmethod
    def ensure_session(self, session_id: str, user_id: str) -> None:
        """校验并登记会话归属；不属于该 user_id 时抛 SessionOwnershipError。"""

    @abstractmethod
    def load_recent(self, session_id: str, limit: int) -> List[Message]:
        """按时间正序返回最近 limit 条消息。"""

    @abstractmethod
    def append(self, items: List[Message]) -> int:
        """追加消息；返回实际写入条数（幂等跳过的不计）。"""

    @abstractmethod
    def exists_request(self, request_id: str) -> bool:
        """该 request_id 是否已处理过。"""

    # ---- 会话列表 / 归属查询 / 删除（后端路由层用，只操作归档数据，不参与对话链路）----
    # 这组不是抽象方法：换 PG 只需在具体实现里覆盖，不影响已有实现类。

    def get_session_owner(self, session_id: str) -> Optional[str]:
        """只读查询会话归属；不存在返回 None（不像 ensure_session 会登记新会话）。"""
        raise NotImplementedError

    def list_sessions(self, user_id: str, limit: int = 50, offset: int = 0) -> List[Dict[str, str]]:
        """该用户的会话列表，按 updated_at 倒序分页。"""
        raise NotImplementedError

    def count_sessions(self, user_id: str) -> int:
        """该用户的会话总数。"""
        raise NotImplementedError

    def delete_session(self, session_id: str) -> int:
        """删除会话及其全部消息；返回删除的消息条数。"""
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - 可选实现
        return None


class SqliteConversationStore(ConversationStore):
    """SQLite 默认实现（通过 SQLAlchemy，换 PG 只需换 URL）。"""

    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._engine: Engine = create_engine(
            f"sqlite+pysqlite:///{db_path}", future=True
        )
        self.init_schema()

    @classmethod
    def from_url(cls, url: str) -> "SqliteConversationStore":
        """备用构造：直接给 SQLAlchemy URL（如 PG）。"""
        obj = cls.__new__(cls)
        obj._engine = create_engine(url, future=True)
        obj.init_schema()
        return obj

    def init_schema(self) -> None:
        metadata.create_all(self._engine)

    def ensure_session(self, session_id: str, user_id: str) -> None:
        with self._engine.begin() as conn:
            row = conn.execute(
                select(sessions.c.user_id, sessions.c.created_at).where(
                    sessions.c.session_id == session_id
                )
            ).first()
            now = Message(session_id=session_id, user_id=user_id, role="user", content="").created_at
            if row is None:
                conn.execute(
                    insert(sessions).values(
                        session_id=session_id,
                        user_id=user_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
                return
            if row.user_id != user_id:
                raise SessionOwnershipError(
                    f"session {session_id} 属于 {row.user_id}，拒绝 {user_id} 访问"
                )
            conn.execute(
                sessions.update()
                .where(sessions.c.session_id == session_id)
                .values(updated_at=now)
            )

    def load_recent(self, session_id: str, limit: int) -> List[Message]:
        if limit <= 0:
            return []
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(messages)
                .where(messages.c.session_id == session_id)
                .order_by(messages.c.id.desc())
                .limit(limit)
            ).all()
        return [
            Message(
                session_id=r.session_id,
                user_id=r.user_id,
                role=r.role,
                content=r.content,
                request_id=r.request_id,
                created_at=r.created_at,
            )
            for r in reversed(rows)
        ]

    def append(self, items: List[Message]) -> int:
        if not items:
            return 0
        # 幂等：同一 request_id 的消息只写一次
        request_id = items[0].request_id
        if request_id and self.exists_request(request_id):
            return 0
        with self._engine.begin() as conn:
            conn.execute(
                insert(messages),
                [item.model_dump() for item in items],
            )
        return len(items)

    def exists_request(self, request_id: str) -> bool:
        if not request_id:
            return False
        with self._engine.connect() as conn:
            count = conn.execute(
                select(func.count())
                .select_from(messages)
                .where(messages.c.request_id == request_id)
            ).scalar_one()
        return count > 0

    def count_messages(self, session_id: str) -> int:
        """调试/验收用：统计某会话的消息数。"""
        with self._engine.connect() as conn:
            return conn.execute(
                select(func.count())
                .select_from(messages)
                .where(messages.c.session_id == session_id)
            ).scalar_one()

    def get_session_owner(self, session_id: str) -> Optional[str]:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(sessions.c.user_id).where(sessions.c.session_id == session_id)
            ).first()
        return None if row is None else row.user_id

    def list_sessions(self, user_id: str, limit: int = 50, offset: int = 0) -> List[Dict[str, str]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(sessions)
                .where(sessions.c.user_id == user_id)
                .order_by(sessions.c.updated_at.desc(), sessions.c.session_id)
                .limit(limit)
                .offset(offset)
            ).all()
        return [
            {
                "session_id": r.session_id,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rows
        ]

    def count_sessions(self, user_id: str) -> int:
        with self._engine.connect() as conn:
            return conn.execute(
                select(func.count()).select_from(sessions).where(sessions.c.user_id == user_id)
            ).scalar_one()

    def delete_session(self, session_id: str) -> int:
        # 先删消息再删会话行；单事务保证两者一致
        with self._engine.begin() as conn:
            removed = conn.execute(
                messages.delete().where(messages.c.session_id == session_id)
            ).rowcount
            conn.execute(sessions.delete().where(sessions.c.session_id == session_id))
        return removed or 0

    def close(self) -> None:
        self._engine.dispose()
