"""题库工具（白名单）。

模型只能调用这里注册的函数；检索由 `QuestionService` 走向量库，
`present_quiz` 是展示型工具——把题卡推给前端，答案不下发，用户作答后由模型判题。

返回值统一是 JSON 字符串（工具结果需要是文本）。
"""

import json
from typing import Any, List

from openjiuwen.core.foundation.tool import LocalFunction, tool

from app.services.question_service import QuestionService


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def make_question_tools(service: QuestionService) -> List[LocalFunction]:
    """把题库检索与题卡展示包装成模型可调用的工具。"""

    async def search_questions(query: str, subject: str = "", limit: int = 5) -> str:
        """在医学考试题库中按语义检索题目，返回题干、选项、答案与解析。

        适用场景：用户明确要求刷题/出题/「考考我」时找配套真题。
        讲解知识点时不要主动检索出题（用户没要求就别调用本工具）。
        拿到题目后如需向用户提问，挑质量最高的一道交给 present_quiz 展示。

        Args:
            query: 检索词，用知识点或题面关键词（如「高血压 降压药物选择」）。
            subject: 可选，限定学科（如「内科」「护理学」「药学」），留空搜全库。
            limit: 返回条数，默认 5，最大 10。

        Returns:
            JSON 字符串：questions 为题目列表（stem/options/answer/analysis/subject/score），
            score 越大越相关；为空说明题库中没有相近题目。
        """
        return _dumps(service.search(query, subject or None, limit))

    async def present_quiz(
        stem: str, options: List[str], subject: str = "", source: str = ""
    ) -> str:
        """把一道单选题以互动题卡的形式展示给用户作答。

        题卡推送到前端后，用户会选择选项提交，其作答作为下一条消息发回。
        纪律：一次只出一题；调用本工具后用一两句话过渡并结束本轮，不要自己公布答案；
        用户作答后先判对错，再对照解析讲解。

        Args:
            stem: 题干（通常取自 search_questions 结果，也可按讲过的知识点自出）。
            options: 选项列表，需带字母前缀（如 ["A. 选项一", "B. 选项二"]），最多 8 个。
            subject: 可选，题目所属学科。
            source: 可选，题目来源，默认 question_bank。

        Returns:
            JSON 字符串：displayed=true 表示题卡已推送。
        """
        return _dumps(service.present_quiz(stem, options, subject, source))

    return [
        tool(search_questions, name="search_questions"),
        tool(present_quiz, name="present_quiz"),
    ]


__all__ = ["make_question_tools"]
