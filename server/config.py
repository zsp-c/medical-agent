"""后端路由层自己的配置：服务监听 / CORS / JWT / 用户库。

约定：Agent 层配置（模型、图谱、存储）仍由 `app.settings` 统一负责；
这里只管 HTTP 层的环境变量。注意 `Settings.load()` 会把 `.env` 写入
os.environ（已存在的环境变量优先），因此本模块必须在它之后读取。
"""

import logging
import os
import secrets
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    # 空 = 允许所有来源（BYOK 无 cookie，不带凭证，无 CSRF 面）；生产建议显式配置
    cors_origins: tuple = ()
    jwt_secret: str = ""
    # 密钥为进程随机生成（重启后已签发令牌全部失效）时为 True，仅适合本地调试
    jwt_secret_ephemeral: bool = False
    jwt_expire_minutes: int = 720  # 12 小时
    allow_registration: bool = True
    user_db_path: str = "data/server.db"
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "ServerConfig":
        origins = os.environ.get("CORS_ORIGINS", "").strip()
        secret = os.environ.get("JWT_SECRET", "").strip()
        ephemeral = False
        if not secret:
            secret = secrets.token_urlsafe(32)
            ephemeral = True
            logger.warning(
                "JWT_SECRET 未配置，已生成随机密钥：服务重启后所有令牌失效（仅限本地调试）"
            )
        return cls(
            host=os.environ.get("SERVER_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=int(os.environ.get("SERVER_PORT", "8000")),
            cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
            jwt_secret=secret,
            jwt_secret_ephemeral=ephemeral,
            jwt_expire_minutes=int(os.environ.get("JWT_EXPIRE_MINUTES", "720")),
            allow_registration=os.environ.get("ALLOW_REGISTRATION", "true").strip().lower()
            not in ("0", "false", "no"),
            user_db_path=os.environ.get("USER_DB_PATH", "data/server.db").strip()
            or "data/server.db",
        )
