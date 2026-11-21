"""练习中心：题卡登记、作答记录与答题统计。

数据契约（两张表，均按 `user_id` 隔离）：

- `quiz_card`：`present_quiz` 每次推送登记一行。**正确答案只存在这里**，
  接口下发题卡时永远不带 `answer`；用户提交后才由 `submit()` 比对。
- `practice_record`：用户提交作答时写一行。`correct` 三态——
  `1` 对 / `0` 错 / `-1` 未判定（问答题、或题目来源没有标准答案时）。

统计不落预计算结果，全部由 `practice_record` 现场聚合（口径改了不用回填）：
- 累计作答、正确率只算 `correct != -1` 的作答，避免把"未判定"算成错
- 连续练习按自然日（`day` 列，本地时区）连续计数
- 趋势按最近 8 个自然周聚合；学科正确率与薄弱点按 `subject` 聚合

设计边界：本服务不调模型、不碰图谱/会话归档；只在答题链路上做登记与聚合，
因此可以被 BYOK 池里的所有 Agent 共享（`AgentPool.practice_service()`）。
"""

import json
import logging
import uuid
from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    select,
)

from app.settings import Settings

logger = logging.getLogger(__name__)

metadata = MetaData()

quiz_cards = Table(
    "quiz_card",
    metadata,
    Column("quiz_id", String(36), primary_key=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("session_id", String(64), nullable=False, default=""),
    Column("kind", String(16), nullable=False, default="choice"),
    Column("stem", String, nullable=False, default=""),
    Column("options", String, nullable=False, default="[]"),
    Column("subject", String(64), nullable=False, default=""),
    Column("source", String(64), nullable=False, default=""),
    # 正确答案（选项字母 A-H）；空串表示来源没有标准答案
    Column("answer", String(16), nullable=False, default=""),
    Column("created_at", String(40), nullable=False, default=""),
)

practice_records = Table(
    "practice_record",
    metadata,
    Column("record_id", String(36), primary_key=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("quiz_id", String(36), nullable=False, index=True),
    Column("session_id", String(64), nullable=False, default=""),
    Column("subject", String(64), nullable=False, default=""),
    Column("kind", String(16), nullable=False, default="choice"),
    Column("stem", String, nullable=False, default=""),
    Column("chosen", String(64), nullable=False, default=""),
    Column("answer", String(16), nullable=False, default=""),
    # 1=正确 / 0=错误 / -1=未判定
    Column("correct", Integer, nullable=False, default=-1),
    Column("day", String(10), nullable=False, index=True),
    Column("created_at", String(40), nullable=False, default=""),
)

LETTERS = "ABCDEFGH"
TREND_WEEKS = 8
WEAK_THRESHOLD = 60.0  # 正确率低于该值的学科标为薄弱
WEAK_MIN_SAMPLES = 3  # 样本太少不参与薄弱点排序


def _now() -> datetime:
    """本地时区当前时间（统计按用户所在时区的自然日聚合）。"""
    return datetime.now().astimezone()


def normalize_choice(value: str, options: Optional[List[str]] = None) -> str:
    """把「我选B：……」「B. 选项文本」「选项文本」归一成选项字母。

    识别不了（问答题的自由作答）时原样返回截断后的文本。
    """
    text = (value or "").strip()
    if not text:
        return ""
    for ch in text[:12]:
        if ch.upper() in LETTERS:
            return ch.upper()
    if options:
        for idx, opt in enumerate(options):
            body = str(opt).strip()
            # 选项通常带「A. 」前缀，比对时去掉
            body = body[2:].strip() if len(body) > 2 and body[1] in ".、．" else body
            if body and (body in text or text in body):
                return LETTERS[idx] if idx < len(LETTERS) else ""
    return text[:32]


def answer_letter(answer: str, options: Optional[List[str]] = None) -> str:
    """标准答案归一成选项字母；给的是选项原文时按内容定位。"""
    text = (answer or "").strip()
    if not text:
        return ""
    head = text[0].upper()
    if head in LETTERS and (len(text) == 1 or text[1] in ".、．)） "):
        return head
    if options:
        for idx, opt in enumerate(options):
            body = str(opt).strip()
            body = body[2:].strip() if len(body) > 2 and body[1] in ".、．" else body
            if body and (body == text or body in text or text in body):
                return LETTERS[idx] if idx < len(LETTERS) else ""
    return head if head in LETTERS and len(text) <= 2 else ""


class PracticeService:
    """题卡与作答的唯一写入点，练习中心统计的唯一读取点。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine = create_engine(f"sqlite:///{settings.sqlite_path}")
        metadata.create_all(self._engine)

    # ------------------------------------------------------------------ #
    # 写入：题卡登记 / 作答提交
    # ------------------------------------------------------------------ #
    def register_quiz(
        self,
        user_id: str,
        session_id: str,
        quiz: Dict[str, Any],
    ) -> Dict[str, Any]:
        """登记一次推送的题卡，返回**可下发前端**的载荷（不含 answer）。

        答案只留在服务端；前端拿到的 `quiz_id` 是提交作答时的唯一凭据。
        """
        quiz_id = uuid.uuid4().hex
        now = _now()
        options = [str(o) for o in (quiz.get("options") or [])]
        record = {
            "quiz_id": quiz_id,
            "user_id": user_id,
            "session_id": session_id or "",
            "kind": quiz.get("kind") or ("choice" if options else "open"),
            "stem": quiz.get("stem") or "",
            "options": json.dumps(options, ensure_ascii=False),
            "subject": (quiz.get("subject") or "")[:64],
            "source": (quiz.get("source") or "")[:64],
            "answer": answer_letter(quiz.get("answer") or "", options),
            "created_at": now.isoformat(timespec="seconds"),
        }
        with self._engine.begin() as conn:
            conn.execute(quiz_cards.insert().values(**record))
        return {
            "quiz_id": quiz_id,
            "kind": record["kind"],
            "stem": record["stem"],
            "options": options,
            "subject": record["subject"],
            "source": record["source"],
        }

    def submit(
        self, user_id: str, quiz_id: str, chosen: str
    ) -> Optional[Dict[str, Any]]:
        """提交一道题的作答，返回判题结果；题卡不存在返回 None。

        幂等：同一 `quiz_id` 重复提交不重复计数（原型里「一题只记一次」），
        直接回放首次结果。他人题卡等同于不存在（不泄露存在性）。
        """
        card = self._get_card(quiz_id)
        if card is None or card["user_id"] != user_id:
            return None
        options = json.loads(card["options"] or "[]")
        picked = normalize_choice(chosen, options)
        existing = self._get_record(quiz_id)
        if existing is not None:
            return self._result(card, existing, already=True)

        correct = self._judge(card, picked, options)
        now = _now()
        record = {
            "record_id": uuid.uuid4().hex,
            "user_id": user_id,
            "quiz_id": quiz_id,
            "session_id": card["session_id"],
            "subject": card["subject"],
            "kind": card["kind"],
            "stem": card["stem"][:200],
            "chosen": picked[:64],
            "answer": card["answer"],
            "correct": correct,
            "day": now.strftime("%Y-%m-%d"),
            "created_at": now.isoformat(timespec="seconds"),
        }
        with self._engine.begin() as conn:
            conn.execute(practice_records.insert().values(**record))
        return self._result(card, record, already=False)

    @staticmethod
    def _judge(card: Dict[str, Any], picked: str, options: List[str]) -> int:
        """1 对 / 0 错 / -1 未判定。没有标准答案（自拟问答题）一律不判定。"""
        answer = card["answer"] or ""
        if card["kind"] == "choice" and answer and picked:
            return 1 if picked == answer else 0
        if card["kind"] == "open" and answer and picked:
            return 1 if answer.strip() == picked.strip() else -1
        return -1

    def _result(
        self, card: Dict[str, Any], record: Dict[str, Any], already: bool
    ) -> Dict[str, Any]:
        correct = int(record["correct"])
        return {
            "quiz_id": card["quiz_id"],
            "correct": None if correct < 0 else bool(correct),
            # 已判定的题才把标准答案回给前端（未提交时永远不会下发）
            "answer": card["answer"] if correct >= 0 else "",
            "chosen": record["chosen"],
            "judged": correct >= 0,
            "already": already,
        }

    # ------------------------------------------------------------------ #
    # 重练：把错题（或指定题卡）复制成一组新题卡
    # ------------------------------------------------------------------ #
    def create_redo(
        self,
        user_id: str,
        quiz_ids: Optional[List[str]] = None,
        subject: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """按错题复制出一组可重新作答的题卡，返回公开载荷（不含 answer）。

        为什么复制而不是复用原 `quiz_id`：提交是幂等的（同一 quiz_id 只记一次），
        复用会让重练直接回放旧结果。复制后是新题卡、新记录，统计里算一次新作答。
        题干相同的题只出现一次；作答记录里的答案留在服务端。
        """
        capped = max(1, min(int(limit or 10), 20))
        cards = self._redo_sources(user_id, quiz_ids, subject, capped)
        out: List[Dict[str, Any]] = []
        for card in cards:
            out.append(
                self.register_quiz(
                    user_id,
                    "",
                    {
                        "kind": card["kind"],
                        "stem": card["stem"],
                        "options": json.loads(card["options"] or "[]"),
                        "subject": card["subject"],
                        "source": card["source"],
                        "answer": card["answer"],
                    },
                )
            )
        return out

    def _redo_sources(
        self,
        user_id: str,
        quiz_ids: Optional[List[str]],
        subject: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """挑出要重练的题卡：显式 quiz_ids 优先，否则取错过的题（新→旧）。"""
        with self._engine.connect() as conn:
            if quiz_ids:
                rows = (
                    conn.execute(
                        select(quiz_cards).where(
                            quiz_cards.c.user_id == user_id,
                            quiz_cards.c.quiz_id.in_(quiz_ids[:limit]),
                        )
                    )
                    .mappings()
                    .all()
                )
                order = {qid: i for i, qid in enumerate(quiz_ids)}
                cards = sorted(
                    (dict(r) for r in rows), key=lambda r: order.get(r["quiz_id"], 1e9)
                )
            else:
                stmt = (
                    select(quiz_cards)
                    .join(
                        practice_records,
                        quiz_cards.c.quiz_id == practice_records.c.quiz_id,
                    )
                    .where(
                        practice_records.c.user_id == user_id,
                        practice_records.c.correct == 0,  # 只重练判错的题
                    )
                    .order_by(practice_records.c.created_at.desc())
                    .limit(limit * 4)
                )
                if subject:
                    stmt = stmt.where(practice_records.c.subject == subject)
                cards = [dict(r) for r in conn.execute(stmt).mappings().all()]

        # 同一道题（题干相同）只留一次，避免"重练 10 题其实是同一题的不同会话"；
        # 没有标准答案的题（自拟问答题）无法判题，不进重练。
        seen: set = set()
        picked: List[Dict[str, Any]] = []
        for card in cards:
            if card.get("kind") != "choice" or not (card.get("answer") or ""):
                continue
            key = (card.get("stem") or "")[:120]
            if not key or key in seen:
                continue
            seen.add(key)
            picked.append(card)
            if len(picked) >= limit:
                break
        return picked

    def wrong_count(self, user_id: str, subject: Optional[str] = None) -> int:
        """去重后、可重练的错题数（有标准答案的选择题）。"""
        with self._engine.connect() as conn:
            stmt = (
                select(practice_records.c.quiz_id, quiz_cards.c.stem)
                .join(quiz_cards, quiz_cards.c.quiz_id == practice_records.c.quiz_id)
                .where(
                    practice_records.c.user_id == user_id,
                    practice_records.c.correct == 0,
                    quiz_cards.c.kind == "choice",
                    quiz_cards.c.answer != "",
                )
            )
            if subject:
                stmt = stmt.where(practice_records.c.subject == subject)
            rows = conn.execute(stmt).all()
        return len({(r[1] or "")[:120] for r in rows})

    # ------------------------------------------------------------------ #
    # 读取：题卡 / 记录回填
    # ------------------------------------------------------------------ #
    def _get_card(self, quiz_id: str) -> Optional[Dict[str, Any]]:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(quiz_cards).where(quiz_cards.c.quiz_id == quiz_id)
            ).mappings().first()
        return dict(row) if row else None

    def _get_record(self, quiz_id: str) -> Optional[Dict[str, Any]]:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(practice_records).where(practice_records.c.quiz_id == quiz_id)
            ).mappings().first()
        return dict(row) if row else None

    def lookup_answers(
        self, user_id: str, quiz_ids: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """批量回填历史消息里的题卡状态（刷新页面后仍能看到对错）。"""
        if not quiz_ids:
            return {}
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(
                    practice_records.c.quiz_id,
                    practice_records.c.chosen,
                    practice_records.c.correct,
                    practice_records.c.answer,
                ).where(
                    practice_records.c.user_id == user_id,
                    practice_records.c.quiz_id.in_(quiz_ids),
                )
            ).mappings()
            return {
                r["quiz_id"]: {
                    "chosen": r["chosen"],
                    "correct": None if int(r["correct"]) < 0 else bool(r["correct"]),
                    "answer": r["answer"] if int(r["correct"]) >= 0 else "",
                }
                for r in rows
            }

    def records(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """最近的作答记录（新→旧）。"""
        with self._engine.begin() as conn:
            rows = conn.execute(
                select(practice_records)
                .where(practice_records.c.user_id == user_id)
                .order_by(practice_records.c.created_at.desc())
                .limit(max(1, min(limit, 200)))
            ).mappings()
        return [
            {
                "record_id": r["record_id"],
                "quiz_id": r["quiz_id"],
                "session_id": r["session_id"],
                "subject": r["subject"] or "未分类",
                "stem": r["stem"],
                "kind": r["kind"],
                "chosen": r["chosen"],
                "answer": r["answer"],
                "correct": None if int(r["correct"]) < 0 else bool(r["correct"]),
                "judged": int(r["correct"]) >= 0,
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # 统计（答题统计页）
    # ------------------------------------------------------------------ #
    def stats(self, user_id: str, days: int = 30) -> Dict[str, Any]:
        """练习中心全量指标。`days` 只影响「近 N 天」与趋势窗口的起点。"""
        days = max(1, min(days, 365))
        today = _now().date()
        window_start = today - timedelta(days=days - 1)
        rows = self._all_records(user_id)

        judged = [r for r in rows if r["correct"] is not None]
        window = [r for r in rows if r["day"] >= window_start.isoformat()]
        window_judged = [r for r in window if r["correct"] is not None]
        wrong = [r for r in rows if r["correct"] is False]

        return {
            "generated_at": _now().isoformat(timespec="seconds"),
            "window_days": days,
            "total_answered": len(rows),
            "window_answered": len(window),
            "accuracy": self._rate(judged),
            "window_accuracy": self._rate(window_judged),
            "wrong_total": len(wrong),
            "pending_review": len([r for r in window if r["correct"] is False]),
            "streak_days": self._streak(rows, today),
            "longest_streak": self._longest_streak(rows),
            "trend": self._trend(rows, today),
            "subjects": self._subjects(judged),
            "weak_points": self._weak_points(judged),
            "recent": self.records(user_id, 8),
            "empty": not rows,
        }

    def _all_records(self, user_id: str) -> List[Dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(
                    practice_records.c.quiz_id,
                    practice_records.c.subject,
                    practice_records.c.correct,
                    practice_records.c.day,
                    practice_records.c.created_at,
                ).where(practice_records.c.user_id == user_id)
            ).mappings()
            return [
                {
                    "quiz_id": r["quiz_id"],
                    "subject": r["subject"] or "未分类",
                    "correct": None if int(r["correct"]) < 0 else bool(r["correct"]),
                    "day": r["day"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    @staticmethod
    def _rate(rows: List[Dict[str, Any]]) -> Optional[float]:
        if not rows:
            return None
        return round(sum(1 for r in rows if r["correct"]) * 100 / len(rows), 1)

    @staticmethod
    def _active_days(rows: List[Dict[str, Any]]) -> List[date]:
        days = set()
        for r in rows:
            try:
                days.add(date.fromisoformat(r["day"]))
            except ValueError:
                continue
        return sorted(days)

    def _streak(self, rows: List[Dict[str, Any]], today: date) -> int:
        """当前连续练习天数：今天没练但昨天练过，仍算连续（当天还没开始不打断）。"""
        days = self._active_days(rows)
        if not days:
            return 0
        cursor = today if today in days else today - timedelta(days=1)
        if cursor not in days:
            return 0
        streak = 0
        while cursor in days:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    def _longest_streak(self, rows: List[Dict[str, Any]]) -> int:
        days = self._active_days(rows)
        best = run = 0
        prev: Optional[date] = None
        for d in days:
            run = run + 1 if prev and (d - prev).days == 1 else 1
            best = max(best, run)
            prev = d
        return best

    def _trend(self, rows: List[Dict[str, Any]], today: date) -> List[Dict[str, Any]]:
        """最近 TREND_WEEKS 个自然周（周一起）的作答量与正确率。"""
        monday = today - timedelta(days=today.weekday())
        buckets: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        for i in range(TREND_WEEKS - 1, -1, -1):
            start = monday - timedelta(weeks=i)
            key = start.isoformat()
            buckets[key] = {
                "week_start": key,
                "label": start.strftime("%m-%d"),
                "answered": 0,
                "correct": 0,
                "judged": 0,
            }
        first = date.fromisoformat(next(iter(buckets)))
        for r in rows:
            try:
                d = date.fromisoformat(r["day"])
            except ValueError:
                continue
            if d < first:
                continue
            key = (d - timedelta(days=d.weekday())).isoformat()
            bucket = buckets.get(key)
            if bucket is None:
                continue
            bucket["answered"] += 1
            if r["correct"] is not None:
                bucket["judged"] += 1
                bucket["correct"] += 1 if r["correct"] else 0
        for bucket in buckets.values():
            judged = bucket.pop("judged")
            bucket["accuracy"] = (
                round(bucket["correct"] * 100 / judged, 1) if judged else None
            )
        return list(buckets.values())

    def _subjects(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        agg: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            item = agg.setdefault(r["subject"], {"answered": 0, "correct": 0})
            item["answered"] += 1
            item["correct"] += 1 if r["correct"] else 0
        out = [
            {
                "subject": name,
                "answered": item["answered"],
                "correct": item["correct"],
                "accuracy": round(item["correct"] * 100 / item["answered"], 1),
            }
            for name, item in agg.items()
        ]
        out.sort(key=lambda x: (-x["answered"], -x["accuracy"]))
        return out

    def _weak_points(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """薄弱点 = 正确率低于阈值且样本足够的学科，按错题数排序。"""
        weak = []
        for item in self._subjects(rows):
            if item["answered"] < WEAK_MIN_SAMPLES or item["accuracy"] >= WEAK_THRESHOLD:
                continue
            weak.append({**item, "wrong": item["answered"] - item["correct"]})
        weak.sort(key=lambda x: (-x["wrong"], x["accuracy"]))
        return weak[:5]

    def close(self) -> None:
        self._engine.dispose()


__all__ = [
    "PracticeService",
    "answer_letter",
    "normalize_choice",
]
