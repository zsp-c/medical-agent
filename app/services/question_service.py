"""题库 RAG 检索服务（题目知识库）。

数据源是公开医学考试题库（39,307 题，见 `scripts/import_question_bank.py`），
向量存在 Chroma 独立集合 `question_bank`（与长期记忆集合物理隔离，全局共享、
不按用户隔离——题库本身无用户私有内容）。

职责与 `GraphQueryService` 对齐：
1. 强制白名单上限（limit 截断显式）
2. 工具事件钩子（流式 SSE 用）与 pending quiz 收集器是实例态，
   **每个 Agent 持有自己的 QuestionService**（与图谱证据收集器同一约定）
3. embedding 走 `EMBEDDING_*` 配置的 OpenAI 兼容端点（本地 bge-m3），
   检索只消耗 embedding，不消耗对话模型配额
"""

import json
import logging
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from app.settings import Settings

logger = logging.getLogger(__name__)

SEARCH_LIMIT_DEFAULT, SEARCH_LIMIT_MAX = 5, 10


def _clamp(value: int, low: int, high: int, default: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


def _embed(texts: List[str], *, url: str, model: str, api_key: str) -> List[List[float]]:
    body = json.dumps({"input": texts, "model": model}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return [item["embedding"] for item in payload["data"]]


class QuestionService:
    """题库语义检索 + 题卡展示的统一入口。"""

    def __init__(self, settings: Settings) -> None:
        import chromadb  # 延迟导入：与记忆引擎共用同一安装

        self._settings = settings
        client = chromadb.PersistentClient(path=settings.chroma_dir)
        # get_collection：集合不存在（未跑导入脚本）时降级为空库，不阻断对话
        try:
            self._collection = client.get_collection(settings.question_collection)
        except Exception:
            logger.warning("题库集合 %s 不存在，检索将返回空", settings.question_collection)
            self._collection = None
        # 工具事件钩子（流式用）：hook(tool_name, phase)
        self._tool_hook: Optional[Callable[[str, str], None]] = None
        # 本轮已推送的题卡（present_quiz 写入，AgentService 读取后清空）
        self._quiz: Optional[Dict[str, Any]] = None

    # ---- 工具事件钩子 / 轮次状态（与 GraphQueryService 同一约定）----
    def set_tool_hook(self, hook: Optional[Callable[[str, str], None]]) -> None:
        self._tool_hook = hook

    def _emit_tool(self, name: str, phase: str) -> None:
        hook = self._tool_hook
        if hook is not None:
            try:
                hook(name, phase)
            except Exception:
                pass  # 事件推送失败不影响工具本体

    def begin_round(self) -> None:
        """每轮对话开始前清空待推送题卡。"""
        self._quiz = None

    def pop_quiz(self) -> Optional[Dict[str, Any]]:
        """取出并清空本轮待推送的题卡（SSE quiz 事件 / 非流式 meta 用）。"""
        quiz = self._quiz
        self._quiz = None
        return quiz

    # ---- 白名单能力 ----
    def search(
        self, query: str, subject: Optional[str] = None, limit: int = SEARCH_LIMIT_DEFAULT
    ) -> Dict[str, Any]:
        """语义检索题库，返回题目列表（含答案与解析）。"""
        self._emit_tool("search_questions", "start")
        try:
            capped = _clamp(limit, 1, SEARCH_LIMIT_MAX, SEARCH_LIMIT_DEFAULT)
            if self._collection is None or not (query or "").strip():
                return {"questions": [], "truncated": False, "hint": "题库不可用或查询为空"}
            vector = _embed(
                [query.strip()],
                url=self._settings.embedding_base_url,
                model=self._settings.embedding_model,
                api_key=self._settings.embedding_api_key,
            )
            kwargs: Dict[str, Any] = {
                "query_embeddings": vector,
                "n_results": capped,
            }
            if subject:
                kwargs["where"] = {"subject": subject}
            result = self._collection.query(**kwargs)
            questions: List[Dict[str, Any]] = []
            metas = (result.get("metadatas") or [[]])[0]
            distances = (result.get("distances") or [[]])[0]
            ids = (result.get("ids") or [[]])[0]
            for qid, meta, dist in zip(ids, metas, distances):
                questions.append(
                    {
                        "question_id": qid,
                        "subject": meta.get("subject", ""),
                        "file": meta.get("file", ""),
                        "stem": meta.get("stem", ""),
                        "options": json.loads(meta.get("options") or "[]"),
                        "answer": meta.get("answer", ""),
                        "analysis": meta.get("analysis", ""),
                        # cosine distance → 相似度分（越大越相关）
                        "score": round(1 - float(dist), 4),
                    }
                )
            return {"questions": questions, "truncated": len(questions) >= capped}
        finally:
            self._emit_tool("search_questions", "end")

    def present_quiz(
        self, stem: str, options: Optional[List[str]] = None, subject: str = "", source: str = ""
    ) -> Dict[str, Any]:
        """把一道题推送给前端展示（答案不下发，用户作答后由你判题讲解）。

        有选项即单选题（用户点选提交），无选项即问答题（用户文字作答）。
        """
        self._emit_tool("present_quiz", "start")
        try:
            cleaned = [str(o).strip() for o in (options or []) if str(o).strip()][:8]
            self._quiz = {
                "kind": "choice" if cleaned else "open",
                "stem": (stem or "").strip(),
                "options": cleaned,
                "subject": (subject or "").strip(),
                "source": (source or "").strip() or "self",
            }
            return {
                "displayed": True,
                "note": "题卡已展示给用户。请用一两句话过渡并结束本轮回答，"
                "不要自己公布答案；等用户作答后再判对错并讲解。",
            }
        finally:
            self._emit_tool("present_quiz", "end")

    def close(self) -> None:  # Chroma PersistentClient 无需显式释放
        return None
