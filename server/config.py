"""后端路由层自己的配置：服务监听 / CORS / 鉴权令牌表。

约定：Agent 层配置（模型、图谱、存储）仍由 `app.settings` 统一负责；
这里只管 HTTP 层的环境变量。注意 `Settings.load()` 会把 `.env` 写入
os.environ（已存在的环境变量优先），因此本模块必须在它之后读取。
"""

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 未配置 API_TOKENS 时的兜底令牌：仅限本地联调，对外部署必须替换
DEFAULT_TOKEN_SPEC = "dev-user:dev-token"


def parse_tokens(spec: str) -> dict:
    """解析 "alice:token-a,bob:token-b" 为 {token: user_id}。"""
    tokens = {}
    for pair in (spec or "").split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        user, _, token = pair.partition(":")
        user, token = user.strip(), token.strip()
        if user and token:
            tokens[token] = user
    if not tokens:
        user, _, token = DEFAULT_TOKEN_SPEC.partition(":")
        tokens[token] = user
        logger.warning("API_TOKENS 未配置，使用兜底令牌 %s（仅限本地联调）", DEFAULT_TOKEN_SPEC)
    return tokens


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    # 空 = 允许所有来源（BYOK 无 cookie，不带凭证，无 CSRF 面）
    cors_origins: tuple = ()
    # token -> user_id
    tokens: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "ServerConfig":
        origins = os.environ.get("CORS_ORIGINS", "").strip()
        return cls(
            host=os.environ.get("SERVER_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=int(os.environ.get("SERVER_PORT", "8000")),
            cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
            tokens=parse_tokens(os.environ.get("API_TOKENS", "")),
        )
