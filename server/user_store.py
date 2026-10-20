"""用户表：后端自有的账号体系（JWT 登录的存储底座）。

- 独立 SQLite 库（默认 data/server.db），与 Agent 层的 app.db 互不干扰
- 口令用 PBKDF2-HMAC-SHA256（60 万次迭代，OWASP 推荐值），stdlib 实现零依赖
- 存储格式：pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>，常数时间比较
- user_id 为 uuid（登录名可改、身份不变）；username 唯一
"""

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy import Column, MetaData, String, Table, create_engine, select

_PBKDF2_ITERATIONS = 600_000
_SCHEME = "pbkdf2_sha256"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")

metadata = MetaData()
users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("username", String, nullable=False, unique=True, index=True),
    Column("password_hash", String, nullable=False),
    Column("created_at", String, nullable=False),
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{_SCHEME}${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, hash_hex = stored.split("$", 3)
        if scheme != _SCHEME:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


class UsernameTakenError(ValueError):
    """用户名已被注册。"""


class UserStore:
    """users 表读写。同步方法由 FastAPI 线程池执行，不阻塞事件循环。"""

    def __init__(self, db_path: str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(f"sqlite:///{path.as_posix()}", future=True)
        metadata.create_all(self._engine)

    def create_user(self, username: str, password: str) -> Dict[str, str]:
        """注册新用户；用户名冲突抛 UsernameTakenError。"""
        user_id = uuid.uuid4().hex
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    users.insert().values(
                        id=user_id,
                        username=username,
                        password_hash=hash_password(password),
                        created_at=_utcnow(),
                    )
                )
        except Exception as exc:
            # SQLite 唯一约束冲突（ IntegrityError ）；其他异常原样上抛
            if "UNIQUE" in str(exc) or "unique" in str(exc).lower():
                raise UsernameTakenError(username) from exc
            raise
        return {"user_id": user_id, "username": username}

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, str]]:
        """校验口令，成功返回 {user_id, username}，失败 None。"""
        with self._engine.connect() as conn:
            row = conn.execute(
                select(users.c.id, users.c.username, users.c.password_hash).where(
                    users.c.username == username
                )
            ).first()
        if row is None or not verify_password(password, row.password_hash):
            return None
        return {"user_id": row.id, "username": row.username}

    def get(self, user_id: str) -> Optional[Dict[str, str]]:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(users.c.id, users.c.username).where(users.c.id == user_id)
            ).first()
        if row is None:
            return None
        return {"user_id": row.id, "username": row.username}

    def close(self) -> None:
        self._engine.dispose()
