"""翻译工具（白名单）。

模型按需调用：图谱返回的英文原始名（实体名 / 关系名）需要中文时才翻译，
不做全量预翻译（直接关系 RPM，见构建细节 §10）。命中缓存不会产生 LLM 请求。
"""

import json
from typing import List

from openjiuwen.core.foundation.tool import LocalFunction, tool

from app.services.translation_service import TranslationService


def make_translation_tool(service: TranslationService) -> LocalFunction:
    """把按需翻译包装成模型可调用的工具。"""

    async def translate_terms(texts: List[str], kind: str = "term") -> str:
        """把英文医学术语翻译成规范中文译名（批量，自动缓存）。

        使用场景：图谱工具返回的 `name` / `name_original` / 关系名是英文时，
        在最终回答里需要用中文表述，就先调用本工具翻译这些术语。
        基因符号、蛋白符号、标准编号会原样保留，翻译结果里仍出现原文是正常现象。

        Args:
            texts: 待翻译的英文术语列表（同一批一次调用即可，不要逐条调用）。
            kind: 术语类别，用于区分缓存：term（实体名，默认）/ relation（关系名）/ other。

        Returns:
            JSON 字符串：translations 为 {原文: 中文译名}；未收录的术语其值等于原文。
        """
        targets = [t for t in (texts or []) if (t or "").strip()]
        mapping = await service.translate_batch(targets, kind or "term")
        return json.dumps(
            {"translations": mapping}, ensure_ascii=False, default=str
        )

    return tool(translate_terms, name="translate_terms")


__all__ = ["make_translation_tool"]
