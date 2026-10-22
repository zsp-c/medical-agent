"""用户上传文件的存储与解析编排。

- 文件落盘：`<upload_dir>/<user_id>/<file_id>/<原名>`，删除时整目录清掉
- 元数据与解析出的纯文本存 SQLite（与会话库同一个 app.db，表 `uploaded_file`）
- 全部读写按 `user_id` 隔离：加载/删除时归属不匹配直接拒绝
- 解析只在上传时做一次；对话每轮只读库，不重复解析
- 图片走视觉模型 caption：优先用请求自带的 BYOK 配置（仅内存使用，不持久化），
  缺省用服务端默认模型；模型不支持视觉时明确报错，不静默吞
"""

import asyncio
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    select,
)

from app.schemas import LLMConfig
from app.settings import Settings
from app.throttle import RateGate, ThrottledModel
from app.utils import file_parsers

logger = logging.getLogger(__name__)

metadata = MetaData()

uploaded_files = Table(
    "uploaded_file",
    metadata,
    Column("file_id", String(36), primary_key=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("name", String(256), nullable=False),
    Column("ext", String(16), nullable=False),
    Column("size", Integer, nullable=False),
    # ready=解析完成可用 / failed=解析失败（error 给用户看）
    Column("status", String(16), nullable=False),
    Column("error", String, nullable=False, default=""),
    Column("text", String, nullable=False, default=""),
    Column("created_at", String(40), nullable=False),
)


class UploadRejectedError(ValueError):
    """类型不支持 / 超限等用户可纠正的问题——message 直接给前端展示。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FileService:
    """上传文件的唯一入口。由 `app.factory.AgentPool` 装配为共享设施。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._upload_root = Path(settings.upload_dir)
        self._engine = create_engine(f"sqlite:///{settings.sqlite_path}")
        metadata.create_all(self._engine)
        # 图片 caption 也计 LLM 请求，与全项目同一套 RPM 滑动窗口策略
        self._gate = RateGate(settings.llm_rpm_limit)

    # ------------------------------------------------------------------ #
    # 上传 + 解析
    # ------------------------------------------------------------------ #
    async def ingest(
        self,
        user_id: str,
        name: str,
        data: bytes,
        llm: Optional[LLMConfig] = None,
    ) -> Dict[str, Any]:
        """落盘 → 解析 → 入库，返回文件记录（含 status，供前端展示解析结果）。

        类型不支持 / 超限时抛 UploadRejectedError（由路由层映射成 4xx）。
        解析失败不抛异常：记录 status=failed + 用户可读 error。
        """
        ext = Path(name).suffix.lower()
        if ext in file_parsers.LEGACY_EXTS:
            new_ext = ".docx" if ext == ".doc" else ".pptx"
            raise UploadRejectedError(
                f"不支持 {ext} 老格式，请另存为 {new_ext} 后重新上传"
            )
        if ext not in file_parsers.SUPPORTED_EXTS:
            raise UploadRejectedError(
                f"不支持的文件类型「{ext or '无扩展名'}」，"
                "支持：.txt/.md/.docx/.pptx 与 .png/.jpg/.jpeg/.webp/.gif"
            )
        max_bytes = self._settings.upload_max_mb * 1024 * 1024
        if len(data) > max_bytes:
            raise UploadRejectedError(
                f"文件超过大小上限（{self._settings.upload_max_mb}MB）"
            )

        file_id = uuid.uuid4().hex
        folder = self._upload_root / user_id / file_id
        folder.mkdir(parents=True, exist_ok=True)
        # 只取文件名部分，防路径穿越
        safe_name = Path(name).name or f"file{ext}"
        path = folder / safe_name
        path.write_bytes(data)

        status, error, text = "ready", "", ""
        try:
            text = await self._parse(str(path), ext, llm)
            if not text.strip():
                status, error = "failed", "未能从文件中提取到文字内容"
        except Exception as exc:
            logger.warning("文件解析失败 name=%s: %s", safe_name, exc)
            status, error = "failed", self._friendly_parse_error(exc, ext)

        record = {
            "file_id": file_id,
            "user_id": user_id,
            "name": safe_name,
            "ext": ext,
            "size": len(data),
            "status": status,
            "error": error,
            "text": text,
            "created_at": _now_iso(),
        }
        with self._engine.begin() as conn:
            conn.execute(uploaded_files.insert().values(**record))
        return self._public(record)

    async def _parse(self, path: str, ext: str, llm: Optional[LLMConfig]) -> str:
        """按扩展名分派解析。本地解析放线程池，避免阻塞事件循环。"""
        if ext in file_parsers.TEXT_EXTS:
            return await asyncio.to_thread(file_parsers.parse_text, path)
        if ext == ".docx":
            return await asyncio.to_thread(file_parsers.parse_docx, path)
        if ext == ".pptx":
            return await asyncio.to_thread(file_parsers.parse_pptx, path)
        # 图片：视觉模型 caption（BYOK 配置仅内存使用；缺省用服务端默认模型）
        model = self._caption_model(llm)
        return await file_parsers.caption_image(path, model)

    def _caption_model(self, llm: Optional[LLMConfig]) -> ThrottledModel:
        api_key = llm.api_key if llm else self._settings.api_key
        base_url = llm.base_url if llm else self._settings.base_url
        model_name = llm.model if llm else self._settings.model
        return ThrottledModel(
            ModelClientConfig(
                client_provider="openai", api_key=api_key, api_base=base_url
            ),
            ModelRequestConfig(model_name=model_name),
            gate=self._gate,
        )

    @staticmethod
    def _friendly_parse_error(exc: Exception, ext: str) -> str:
        if ext in file_parsers.IMAGE_EXTS:
            return (
                "图片解析失败：需要视觉模型（如 qwen-vl、gpt-4o）才能理解图片，"
                "当前模型不支持或调用失败"
            )
        return "文件解析失败，请确认文件未损坏后重试"

    # ------------------------------------------------------------------ #
    # 查询 / 删除
    # ------------------------------------------------------------------ #
    def list_files(self, user_id: str) -> List[Dict[str, Any]]:
        """当前用户的文件列表（新→旧），不含正文。"""
        with self._engine.begin() as conn:
            rows = conn.execute(
                select(uploaded_files)
                .where(uploaded_files.c.user_id == user_id)
                .order_by(uploaded_files.c.created_at.desc())
            ).mappings()
        return [self._public(dict(r)) for r in rows]

    def delete_file(self, user_id: str, file_id: str) -> Optional[bool]:
        """删除文件（磁盘目录 + 数据行）。None=不存在，False=属于他人，True=已删。"""
        with self._engine.begin() as conn:
            owner = conn.execute(
                select(uploaded_files.c.user_id).where(
                    uploaded_files.c.file_id == file_id
                )
            ).scalar_one_or_none()
            if owner is None:
                return None
            if owner != user_id:
                return False
            conn.execute(
                delete(uploaded_files).where(uploaded_files.c.file_id == file_id)
            )
        shutil.rmtree(self._upload_root / user_id / file_id, ignore_errors=True)
        return True

    def load_texts(self, user_id: str, file_ids: List[str]) -> List[Dict[str, str]]:
        """按 id 取「就绪且属于该用户」的文件正文，供注入本轮对话。

        每份正文按 upload_char_limit 截断并标注；不存在/未就绪/他人的文件静默跳过
        （前端只应传自己列表里的就绪文件，漏掉即为异常用法，不为此阻断对话）。
        """
        if not file_ids:
            return []
        limit = self._settings.upload_char_limit
        blocks: List[Dict[str, str]] = []
        with self._engine.begin() as conn:
            rows = conn.execute(
                select(
                    uploaded_files.c.name,
                    uploaded_files.c.text,
                ).where(
                    uploaded_files.c.file_id.in_(file_ids[: self._settings.upload_max_files]),
                    uploaded_files.c.user_id == user_id,
                    uploaded_files.c.status == "ready",
                )
            ).all()
        for name, text in rows:
            if len(text) > limit:
                text = text[:limit] + "\n……（文件过长，以上内容已截断）"
            blocks.append({"name": name, "text": text})
        return blocks

    def close(self) -> None:
        self._engine.dispose()

    @staticmethod
    def _public(record: Dict[str, Any]) -> Dict[str, Any]:
        """对外视图：不返回正文与 user_id。"""
        return {
            "file_id": record["file_id"],
            "name": record["name"],
            "size": record["size"],
            "status": record["status"],
            "error": record["error"],
            "chars": len(record.get("text") or ""),
            "created_at": record["created_at"],
        }
