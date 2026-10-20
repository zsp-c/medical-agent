"""按需翻译服务（带缓存）。

用途：图谱里的实体/关系多为英文原始名（PrimeKG 风格），回答时按需转中文。
翻译由**模型按需触发**（工具），不是每轮无脑全量翻译 —— 直接关系 RPM（§10）。

硬约束（构建细节 §10）：
- 缓存键参与哈希的因子包含 `prompt_version`：改了提示词旧译名立即失效
- 原文不可覆盖：缓存只存 `source_text → translated_text`，回填时以缓存为准
- 基因/蛋白符号与标准编号**原样保留**，不译
- 模型无法确定的术语标 `pending`，**不臆造**译名；调用方拿到的仍是原文
- 批量：一批术语只发**一次** LLM 请求（同批内 miss 合并），并过全局限流闸门
- 缓存命中时**不产生任何 LLM 请求**（这是本模块的核心验收点）
"""

import json
import logging
import re
from typing import Dict, Iterable, List, Optional

from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)

from app.repositories.translation_cache import TranslationCache, make_cache_key
from app.settings import Settings
from app.throttle import RateGate, ThrottledModel

logger = logging.getLogger(__name__)

# 提示词版本：改动下面这段话必须同时改这个版本号（否则旧缓存会被继续复用）
PROMPT_VERSION = "v1"

TARGET_LANGUAGE = "zh"

_SYSTEM_PROMPT = (
    "你是医学知识图谱的术语翻译器。把用户给出的英文医学名词翻译成规范中文译名。\n"
    "规则：\n"
    "1. 只输出 JSON 对象，键是原文（必须与输入完全一致），值是中文译名。\n"
    "2. 基因符号、蛋白符号、标准编号（如 ACE、AGT、HGNC:3535、OMIM:106150）"
    "原样返回，不作翻译。\n"
    "3. 无法确定规范译名的，值填空字符串，不要臆造、不要音译。\n"
    "4. 不要输出解释、不要加代码块标记。"
)


def _extract_json_object(text: str) -> Optional[dict]:
    """从模型输出里抠出第一个 JSON 对象（容忍 ```json 围栏与前后噪声）。"""
    if not text:
        return None
    cleaned = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        logger.warning("翻译结果不是合法 JSON，原文片段：%s", cleaned[:200])
        return None
    return data if isinstance(data, dict) else None


class TranslationService:
    """批量翻译 + 缓存。

    `translate_batch` 返回 `{原文: 译名}`：命中缓存或新翻译成功的给中文，
    模型无法确定的给**原文**（同时以 `pending` 落库，下次仍可重试）。
    """

    def __init__(
        self,
        cache: TranslationCache,
        *,
        settings: Settings,
        gate: Optional[RateGate] = None,
    ) -> None:
        self._cache = cache
        self._model_name = settings.model
        self._api_key = settings.api_key
        self._base_url = settings.base_url
        self._gate = gate
        self._model: Optional[ThrottledModel] = None
        # 验收用：本进程累计真实 LLM 请求次数
        self.llm_calls = 0

    # ---- 模型 ----

    def _get_model(self) -> ThrottledModel:
        if self._model is None:
            # 复用与 Agent 相同的 OpenAI 兼容配置（provider 固定 openai）
            client = ModelClientConfig(
                client_provider="openai",
                api_key=self._api_key,
                api_base=self._base_url,
            )
            cfg = ModelRequestConfig(model_name=self._model_name)
            self._model = ThrottledModel(client, cfg, gate=self._gate)
        return self._model

    # ---- 对外 ----

    async def translate_batch(
        self, texts: Iterable[str], kind: str = "term", language: str = TARGET_LANGUAGE
    ) -> Dict[str, str]:
        """批量翻译；`kind` 区分实体名 / 关系名 / 属性值等，参与缓存键。"""
        unique: List[str] = []
        for raw in texts or []:
            text = (raw or "").strip()
            if text and text not in unique:
                unique.append(text)
        if not unique:
            return {}

        keys = {
            text: make_cache_key(
                text, kind, language, self._model_name, PROMPT_VERSION
            )
            for text in unique
        }
        cached = self._cache.get_many(list(keys.values()))

        result: Dict[str, str] = {}
        misses: List[str] = []
        for text in unique:
            hit = cached.get(keys[text])
            if hit:
                result[text] = hit
            else:
                misses.append(text)

        if misses:
            translated = await self._translate_misses(misses, kind)
            result.update(translated)

        # 未拿到译名的（模型不确定 / 请求失败）回落到原文
        for text in unique:
            result.setdefault(text, text)
        return result

    async def translate_one(
        self, text: str, kind: str = "term", language: str = TARGET_LANGUAGE
    ) -> str:
        """单条翻译的便捷入口（内部仍是批量路径）。"""
        return (await self.translate_batch([text], kind, language)).get(text, text)

    def close(self) -> None:
        self._cache.close()

    # ---- 内部 ----

    async def _translate_misses(self, misses: List[str], kind: str) -> Dict[str, str]:
        """一批 miss 只发一次请求；失败或模型不确定的按 pending 处理。"""
        try:
            self.llm_calls += 1
            # 注意：不要显式传 temperature —— kimi-k2.6 只接受固定采样参数，
            # 显式给值会被服务端以 400 invalid temperature 拒绝；不传（temperature=None）
            # 则不下发该参数，DeepSeek 等厂商同样兼容。
            reply = await self._get_model().invoke(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"kind": kind, "terms": misses}, ensure_ascii=False
                        ),
                    },
                ],
            )
            mapping = _extract_json_object(getattr(reply, "content", "") or "")
        except Exception:
            logger.warning("批量翻译失败，本批回落原文", exc_info=True)
            return {}

        if mapping is None:
            return {}

        rows: List[dict] = []
        for text in misses:
            value = (mapping.get(text) or "").strip()
            if value:
                rows.append(self._row(text, value, kind, "ok"))
            else:
                # 模型不确定：不臆造，落 pending（译名为空，下次仍会重试）
                rows.append(self._row(text, "", kind, "pending"))
        try:
            self._cache.put_many(rows)
        except Exception:
            logger.warning("翻译缓存写入失败，本次结果仍可用", exc_info=True)

        return {
            row["source_text"]: row["translated_text"]
            for row in rows
            if row["status"] == "ok"
        }

    def _row(self, source_text: str, translated: str, kind: str, status: str) -> dict:
        return {
            "cache_key": make_cache_key(
                source_text, kind, TARGET_LANGUAGE, self._model_name, PROMPT_VERSION
            ),
            "source_text": source_text,
            "kind": kind,
            "language": TARGET_LANGUAGE,
            "model_name": self._model_name,
            "prompt_version": PROMPT_VERSION,
            "translated_text": translated,
            "status": status,
        }


__all__ = ["TranslationService", "PROMPT_VERSION", "TARGET_LANGUAGE"]
