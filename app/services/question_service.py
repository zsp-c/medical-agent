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
import random
import re
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from app.settings import Settings

logger = logging.getLogger(__name__)

SEARCH_LIMIT_DEFAULT, SEARCH_LIMIT_MAX = 5, 10
MAX_CACHED_QUESTIONS = 10  # 最近一次检索结果缓存条数（present_quiz 回填正确答案用）


def _norm_stem(text: str) -> str:
    """题干归一：去空白与常见标点，用于判定"题卡是否取自刚才检索的题"。"""
    return re.sub(r"[\s，。、；：？！“”‘’（）()《》〈〉·,.;:?!\"']", "", text or "")


def _clamp(value: int, low: int, high: int, default: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


def _embed(
    texts: List[str], *, url: str, model: str, api_key: str, timeout: float = 120
) -> List[List[float]]:
    body = json.dumps({"input": texts, "model": model}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return [item["embedding"] for item in payload["data"]]


def embedding_ready(settings: Settings, timeout: float = 1.5) -> bool:
    """探测 embedding 端点是否可用（`/api/meta` 的状态标记用，短超时不拖慢响应）。

    题库检索与长期记忆共用这套 `EMBEDDING_*` 配置：端点不可用时两者都会降级
    （检索返回空并带 hint），但对话本身不受影响。
    """
    if not (settings.embedding_base_url or "").strip():
        return False
    try:
        _embed(
            ["ping"],
            url=settings.embedding_base_url,
            model=settings.embedding_model,
            api_key=settings.embedding_api_key,
            timeout=timeout,
        )
        return True
    except Exception:
        return False


def count_questions(settings: Settings) -> int:
    """题库总量（登录页/侧栏的规模标签用）；集合不存在或不可用时返回 0。"""
    try:
        import chromadb

        client = chromadb.PersistentClient(path=settings.chroma_dir)
        return int(client.get_collection(settings.question_collection).count())
    except Exception:
        return 0


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
        # 最近一次检索到的题（含答案）：模型转手交给 present_quiz 时据此回填标准答案，
        # 这样练习中心能自动判对错，而不依赖模型"记得"把答案传进来
        self._recent: List[Dict[str, Any]] = []

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
            try:
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
            except Exception as exc:  # embedding 端点不可用 / 集合损坏 → 降级不抛错
                logger.warning("题库检索降级（%s）：%s", type(exc).__name__, exc)
                return {
                    "questions": [],
                    "truncated": False,
                    "degraded": True,
                    "hint": (
                        "题库语义检索当前不可用（向量服务未连接），本轮没有检索到真题；"
                        "可以按知识点自拟题目（present_quiz 仍可用），或直接讲解知识点"
                    ),
                }
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
            self._remember(questions)
            return {"questions": questions, "truncated": len(questions) >= capped}
        finally:
            self._emit_tool("search_questions", "end")

    def sample_questions(
        self, limit: int = 5, subject: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """随机抽题（练习中心组卷用），**不走向量检索**。

        直接按随机 offset 从集合取元数据，因此不依赖 embedding 端点——
        向量服务挂了也能出题，只是无法按语义相关性挑题。
        """
        if self._collection is None:
            return []
        capped = _clamp(limit, 1, 20, 5)
        try:
            total = int(self._collection.count())
        except Exception:
            return []
        if total <= 0:
            return []
        # 逐题随机取：题库按学科成块存放，一次性连续 offset 会抽到同一学科，
        # 分散取样能让一组题覆盖多个学科（最多尝试 4 倍次数，避免死循环）。
        questions: List[Dict[str, Any]] = []
        seen_ids: set = set()
        seen_stems: set = set()
        attempts = 0
        while len(questions) < capped and attempts < capped * 6:
            attempts += 1
            kwargs: Dict[str, Any] = {"limit": 1, "include": ["metadatas"]}
            kwargs["offset"] = random.randrange(0, total)
            if subject:
                kwargs["where"] = {"subject": subject}
            try:
                result = self._collection.get(**kwargs)
            except Exception as exc:  # 集合损坏 / where 非法 → 空结果，不抛给接口
                logger.warning("题库随机抽题失败（%s）：%s", type(exc).__name__, exc)
                return questions
            ids = result.get("ids") or []
            metas = result.get("metadatas") or []
            if not ids or not metas:
                continue
            qid, meta = ids[0], metas[0] or {}
            if qid in seen_ids:
                continue
            options = json.loads(meta.get("options") or "[]")
            stem = meta.get("stem") or ""
            if len(options) < 2 or not stem or stem[:80] in seen_stems:
                continue  # 选择题才有得练；同一题只取一次
            seen_ids.add(qid)
            seen_stems.add(stem[:80])
            questions.append(
                {
                    "question_id": qid,
                    "subject": meta.get("subject", ""),
                    "file": meta.get("file", ""),
                    "stem": stem,
                    "options": options,
                    "answer": meta.get("answer", ""),
                    "analysis": meta.get("analysis", ""),
                    "score": None,
                }
            )
        return questions

    def _remember(self, questions: List[Dict[str, Any]]) -> None:
        """缓存本次检索结果，供 present_quiz 回填标准答案（答案不出这个服务）。"""
        self._recent = [
            {
                "norm": _norm_stem(q.get("stem", "")),
                "options": q.get("options") or [],
                "answer": q.get("answer", ""),
            }
            for q in questions[:MAX_CACHED_QUESTIONS]
        ]

    def _match_answer(self, stem: str) -> str:
        """题卡题干能在最近检索结果里对上，就取那道题的标准答案。"""
        target = _norm_stem(stem)
        if not target:
            return ""
        for item in self._recent:
            norm = item["norm"]
            if not norm:
                continue
            if norm == target:
                return item["answer"]
            # 模型可能截断/补全题干：短的一方足够长且被包含时也认
            if min(len(norm), len(target)) >= 12 and (norm in target or target in norm):
                return item["answer"]
        return ""

    def present_quiz(
        self,
        stem: str,
        options: Optional[List[str]] = None,
        subject: str = "",
        source: str = "",
        answer: str = "",
    ) -> Dict[str, Any]:
        """把一道题推送给前端展示（答案不下发，用户作答后由你判题讲解）。

        有选项即单选题（用户点选提交），无选项即问答题（用户文字作答）。
        标准答案用于练习中心自动判题：优先用显式传入的 `answer`，
        其次按题干在最近一次检索结果里回填；都没有则记为"未判定"。
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
                "answer": (answer or "").strip() or self._match_answer(stem),
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
