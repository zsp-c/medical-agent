"""P01 注册/登录：口令哈希与账号表（server/user_store.py）。

只用 stdlib + SQLAlchemy，数据库落在 pytest 的临时目录，跑完即弃。
侧重点在「错的时候必须明确说错」：畸形的哈希串要返回 False 而不是抛异常，
重复用户名要转成 UsernameTakenError 而不是漏出数据库驱动的原始异常。
"""

import pytest

from server.user_store import (
    USERNAME_RE,
    UsernameTakenError,
    UserStore,
    hash_password,
    verify_password,
)

ITERATIONS = 600_000


@pytest.fixture(scope="module")
def stored_hash() -> str:
    """PBKDF2 迭代 60 万次，模块内只算一次，避免每个用例都等半秒。"""
    return hash_password("Passw0rd!")


@pytest.fixture()
def store(tmp_path) -> UserStore:
    s = UserStore(str(tmp_path / "server.db"))
    yield s
    s.close()


def test_hash_format_is_self_describing(stored_hash: str) -> None:
    scheme, iterations, salt_hex, hash_hex = stored_hash.split("$", 3)
    assert scheme == "pbkdf2_sha256"
    assert int(iterations) == ITERATIONS
    assert len(salt_hex) == 32  # 16 字节盐
    assert len(hash_hex) == 64  # SHA-256 摘要
    # 明文绝不能出现在存储串里
    assert "Passw0rd!" not in stored_hash


def test_hash_uses_random_salt() -> None:
    assert hash_password("same-password") != hash_password("same-password")


def test_verify_accepts_correct_password(stored_hash: str) -> None:
    assert verify_password("Passw0rd!", stored_hash) is True


def test_verify_rejects_wrong_password(stored_hash: str) -> None:
    assert verify_password("passw0rd!", stored_hash) is False
    assert verify_password("", stored_hash) is False


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "not-a-hash",
        "pbkdf2_sha256$600000$abc",  # 段数不足
        "bcrypt$600000$aabb$ccdd",  # 方案不符
        "pbkdf2_sha256$600000$zzzz$ccdd",  # 盐不是合法十六进制
        "pbkdf2_sha256$notanint$aabb$ccdd",  # 迭代次数不是数字
    ],
)
def test_verify_returns_false_on_malformed_hash(malformed: str) -> None:
    """存储串损坏时应判为不通过，不能把 ValueError 抛给调用方（会变成 500）。"""
    assert verify_password("Passw0rd!", malformed) is False


def test_create_then_authenticate(store: UserStore) -> None:
    created = store.create_user("zkk_123", "Passw0rd!")
    assert created["username"] == "zkk_123"
    assert len(created["user_id"]) == 32  # uuid4().hex

    got = store.authenticate("zkk_123", "Passw0rd!")
    assert got == {"user_id": created["user_id"], "username": "zkk_123"}


def test_authenticate_rejects_wrong_password(store: UserStore) -> None:
    store.create_user("zkk_123", "Passw0rd!")
    assert store.authenticate("zkk_123", "wrong") is None


def test_authenticate_unknown_user(store: UserStore) -> None:
    assert store.authenticate("nobody", "Passw0rd!") is None


def test_duplicate_username_raises_domain_error(store: UserStore) -> None:
    store.create_user("zkk_123", "Passw0rd!")
    with pytest.raises(UsernameTakenError):
        store.create_user("zkk_123", "another")


def test_get_returns_user_or_none(store: UserStore) -> None:
    created = store.create_user("zkk_123", "Passw0rd!")
    assert store.get(created["user_id"])["username"] == "zkk_123"
    assert store.get("nonexistent") is None


@pytest.mark.parametrize("name", ["abc", "zkk_123", "A-b_c", "a" * 32])
def test_username_pattern_accepts_valid(name: str) -> None:
    assert USERNAME_RE.match(name)


@pytest.mark.parametrize("name", ["ab", "a" * 33, "有中文", "with space", "dot.name", ""])
def test_username_pattern_rejects_invalid(name: str) -> None:
    assert USERNAME_RE.match(name) is None
