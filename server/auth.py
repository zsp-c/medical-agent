"""JWT 鉴权：注册/登录签发令牌，业务接口校验 Bearer JWT → user_id。

user_id 只在服务端由用户表产出（接口文档铁律：「user_id 必须来自后端鉴权
结果，不得由前端传入」）。令牌为 HS256 JWT，payload：sub=user_id、iat、exp。
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt
from fastapi import HTTPException, Request, status

from server.config import ServerConfig

_ALGORITHM = "HS256"


def create_access_token(cfg: ServerConfig, user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=cfg.jwt_expire_minutes),
    }
    return jwt.encode(payload, cfg.jwt_secret, algorithm=_ALGORITHM)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_token(cfg: ServerConfig, token: str) -> str:
    """校验令牌并返回 user_id；失败抛 401。"""
    try:
        payload: Dict[str, Any] = jwt.decode(token, cfg.jwt_secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("令牌已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("令牌无效") from exc
    user_id = payload.get("sub")
    if not user_id:
        raise _unauthorized("令牌无效")
    return user_id


def current_user(request: Request) -> str:
    """FastAPI 依赖：从 Authorization: Bearer <jwt> 解析出 user_id。"""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.strip().lower() != "bearer" or not token.strip():
        raise _unauthorized("缺少 Bearer 令牌")
    return decode_token(request.app.state.server_config, token.strip())
