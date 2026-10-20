"""简单 Token 鉴权：Authorization: Bearer <token> → user_id。

user_id 只在服务端由令牌表产出，前端无法伪造（接口文档铁律：
「user_id 必须来自后端鉴权结果，不得由前端传入」）。令牌表来自
环境变量 API_TOKENS（"alice:token-a,bob:token-b"），比对用常数时间比较。
"""

import hmac

from fastapi import HTTPException, Request, status

from server.config import ServerConfig


def current_user(request: Request) -> str:
    cfg: ServerConfig = request.app.state.server_config
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.strip().lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Bearer 令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = token.strip()
    for known, user_id in cfg.tokens.items():
        if hmac.compare_digest(known, token):
            return user_id
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="令牌无效",
        headers={"WWW-Authenticate": "Bearer"},
    )
