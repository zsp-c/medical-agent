"""翻译缓存存储。

翻译是**确定性映射**（同一个术语 → 同一个译名），与可演化的长期记忆天然不同，
因此单独一张表，不进记忆引擎。

缓存键 = `sha256(原文 + kind + 目标语言 + 模型名 + prompt 版本)`：
其中 prompt 版本必须参与哈希，否则改动提示词后旧译名会被当成"仍然有效"。
这样"同一术语第二次出现不产生 LLM 请求"就是一次主键命中，而不是靠模糊匹配。
"""

import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from sqlalchemy import Column, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.engine import Engine

metadata = MetaData()

translation_cache = Table(
    "translation_cache",
    metadata,
    Column("cache_key", String(64), primary_key=True),
    Column("source_text", String(512), nullable=False, index=True),
    Column("kind", String(32), nullable=False),
    Column("language", String(16), nullable=False),
    Column("model_name", String(64), nullable=False),
    Column("prompt_version", String(32), nullable=False),
    Column("translated_text", String(1024), nullable=False, default=""),
    # ok=可用；pending=模型无法确定，暂不采用（不臆造译名）
    Column("status", String(16), nullable=False, default="ok"),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)


def make_cache_key(
    source_text: str, kind: str, language: str, model_name: str, prompt_version: str
) -> str:
    """缓存键（见模块文档）。"""
    raw = "\x1f".join([source_text, kind, language, model_name, prompt_version])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TranslationCache(ABC):
    """翻译缓存接口（将来换 Redis/PG 只换实现）。"""

    @abstractmethod
    def get_many(self, keys: list[str]) -> Dict[str, str]:
        """批量取；返回 {cache_key: translated_text}，只含 status=ok 的记录。"""

    @abstractmethod
    def put_many(self, rows: list[dict]) -> int:
        """批量写（按 cache_key 覆盖更新）；返回写入条数。"""

    def close(self) -> None:  # pragma: no cover - 可选实现
        return None


class SqliteTranslationCache(TranslationCache):
    """SQLite 默认实现（与 ConversationStore 同一套 SQLAlchemy 用法，便于换 PG）。"""

    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._engine: Engine = create_engine(
            f"sqlite+pysqlite:///{db_path}", future=True
        )
        self.init_schema()

    @classmethod
    def from_url(cls, url: str) -> "SqliteTranslationCache":
        obj = cls.__new__(cls)
        obj._engine = create_engine(url, future=True)
        obj.init_schema()
        return obj

    def init_schema(self) -> None:
        metadata.create_all(self._engine)

    def get_many(self, keys: list[str]) -> Dict[str, str]:
        if not keys:
            return {}
        hit: Dict[str, str] = {}
        with self._engine.connect() as conn:
            # 分批查询，避免 SQLite 变量数量上限
            for start in range(0, len(keys), 200):
                chunk = keys[start : start + 200]
                rows = conn.execute(
                    select(
                        translation_cache.c.cache_key,
                        translation_cache.c.translated_text,
                    ).where(
                        translation_cache.c.cache_key.in_(chunk),
                        translation_cache.c.status == "ok",
                    )
                ).all()
                hit.update({r.cache_key: r.translated_text for r in rows})
        return hit

    def put_many(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        payload = [{**row, "created_at": now, "updated_at": now} for row in rows]
        with self._engine.begin() as conn:
            # 已存在则更新译名，避免主键冲突
            for row in payload:
                exists = conn.execute(
                    select(translation_cache.c.cache_key).where(
                        translation_cache.c.cache_key == row["cache_key"]
                    )
                ).first()
                if exists:
                    conn.execute(
                        translation_cache.update()
                        .where(translation_cache.c.cache_key == row["cache_key"])
                        .values(
                            translated_text=row["translated_text"],
                            status=row["status"],
                            updated_at=now,
                        )
                    )
                else:
                    conn.execute(insert(translation_cache).values(**row))
        return len(payload)

    def count(self) -> int:
        """调试/验收用。"""
        from sqlalchemy import func

        with self._engine.connect() as conn:
            return conn.execute(
                select(func.count()).select_from(translation_cache)
            ).scalar_one()

    def close(self) -> None:
        self._engine.dispose()


__all__ = [
    "TranslationCache",
    "SqliteTranslationCache",
    "make_cache_key",
    "translation_cache",
]
