"""P01 登录链路：JWT 签发与校验（server/auth.py）。

这些用例只依赖 fastapi + PyJWT，不启动服务、不连 Neo4j / Chroma / 模型，
因此可以在任何机器上直接 `pytest tests/` 跑完。

覆盖的边界：
- 正常签发 → 解析回同一个 user_id
- 过期令牌、换密钥签名、payload 缺 sub 三种伪造/失效形态
- `current_user` 从 Authorization 头取令牌的四种异常形态（缺头 / 非 Bearer / 空令牌 / 多余空格）
"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from server.auth import create_access_token, current_user, decode_token
from server.config import ServerConfig

# 固定密钥：单元测试不依赖环境变量，也不写进任何真实配置文件。
# 长度 ≥32 字节，避免 PyJWT 对 HS256 弱密钥告警掩盖真实问题
CFG = ServerConfig(jwt_secret="unit-test-secret-0123456789abcdef", jwt_expire_minutes=30)


class _State:
    def __init__(self, cfg: ServerConfig) -> None:
        self.server_config = cfg


class _App:
    """最小 request.app 替身：current_user 只读取 app.state.server_config。"""

    def __init__(self, cfg: ServerConfig) -> None:
        self.state = _State(cfg)


def make_request(headers: dict, cfg: ServerConfig = CFG) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/auth/me",
            # Starlette 的 Headers 要求小写键 + bytes 值
            "headers": [
                (k.lower().encode("latin-1"), v.encode("latin-1"))
                for k, v in headers.items()
            ],
            "app": _App(cfg),
        }
    )


def test_token_roundtrip_returns_same_user_id() -> None:
    token = create_access_token(CFG, "user-123")
    assert decode_token(CFG, token) == "user-123"


def test_token_carries_issued_at_and_expiry() -> None:
    token = create_access_token(CFG, "user-123")
    payload = jwt.decode(token, CFG.jwt_secret, algorithms=["HS256"])
    assert payload["sub"] == "user-123"
    assert payload["exp"] - payload["iat"] == CFG.jwt_expire_minutes * 60


def test_expired_token_is_rejected() -> None:
    now = datetime.now(timezone.utc)
    expired = jwt.encode(
        {"sub": "user-123", "iat": now - timedelta(hours=2), "exp": now - timedelta(seconds=1)},
        CFG.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        decode_token(CFG, expired)
    assert exc.value.status_code == 401
    assert "过期" in exc.value.detail
    # 401 必须带 WWW-Authenticate，否则浏览器/前端拿不到「该重新登录」的信号
    assert exc.value.headers["WWW-Authenticate"] == "Bearer"


def test_token_signed_with_another_secret_is_rejected() -> None:
    forged = jwt.encode({"sub": "attacker", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                        "not-the-real-secret-0123456789abcdef", algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        decode_token(CFG, forged)
    assert exc.value.status_code == 401
    assert "无效" in exc.value.detail


def test_token_without_subject_is_rejected() -> None:
    no_sub = jwt.encode(
        {"exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        CFG.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        decode_token(CFG, no_sub)
    assert exc.value.status_code == 401


def test_current_user_accepts_bearer_header() -> None:
    token = create_access_token(CFG, "user-123")
    assert current_user(make_request({"Authorization": f"Bearer {token}"})) == "user-123"


def test_current_user_is_case_insensitive_on_scheme() -> None:
    token = create_access_token(CFG, "user-123")
    assert current_user(make_request({"Authorization": f"bearer {token}"})) == "user-123"


def test_current_user_tolerates_extra_spaces() -> None:
    token = create_access_token(CFG, "user-123")
    assert current_user(make_request({"Authorization": f"Bearer    {token}  "})) == "user-123"


def test_current_user_rejects_missing_header() -> None:
    with pytest.raises(HTTPException) as exc:
        current_user(make_request({}))
    assert exc.value.status_code == 401
    assert "Bearer" in exc.value.detail


@pytest.mark.parametrize(
    "header",
    [
        "",
        "Bearer",
        "Bearer ",
        "Basic dXNlcjpwYXNz",
        "Token abc.def.ghi",
    ],
)
def test_current_user_rejects_malformed_header(header: str) -> None:
    with pytest.raises(HTTPException) as exc:
        current_user(make_request({"Authorization": header}))
    assert exc.value.status_code == 401
