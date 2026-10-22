"""把医学考试题库全量嵌入 Chroma（RAG 检索库）。

数据源：https://github.com/lengfeng650-star/medical-exam-question-bank-json
（克隆到 `data/question_bank/`，`data/subjects/<学科>/*.json`，39,307 题）

设计要点：
- 每题一条文档：embedding 文本为「学科 | 文件标题 | 题干 | 选项」，
  答案与解析只进 metadata（检索按题面语义匹配，不参与向量化）。
- 向量走本地 embedding 端点（`EMBEDDING_BASE_URL`，OpenAI 兼容），与记忆引擎共用
  同一个 bge-m3（1024 维）；**切换 embedding 模型后必须删集合重建**（向量空间不兼容）。
- 独立集合 `question_bank`：与长期记忆集合物理隔离；题库是公开数据，全局共享，
  不按 user_id 隔离。
- 幂等断点续跑：question_id = sha1(相对路径#index)，启动时拉一次已有 id 跳过。

用法：
    python scripts/import_question_bank.py                # 全量导入（断点续跑）
    python scripts/import_question_bank.py --limit 64     # 小批量验证
    python scripts/import_question_bank.py --dry-run      # 只统计题目数
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BANK = ROOT / "data" / "question_bank"
COLLECTION = "question_bank"

EMBED_BATCH = 32      # TEI CPU 单批 32 条（与容器 --max-concurrent-requests 对齐）
CHROMA_BATCH = 500    # Chroma 单批写入


def _load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def iter_questions(bank_dir: Path) -> Iterator[Dict[str, Any]]:
    """遍历全部题库 JSON，逐题产出规范化记录。"""
    subjects_dir = bank_dir / "data" / "subjects"
    for file_path in sorted(subjects_dir.rglob("*.json")):
        subject = file_path.parent.name
        rel = file_path.relative_to(bank_dir).as_posix()
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        file_title = str(payload.get("title") or file_path.stem)
        for q in payload.get("questions") or []:
            stem = str(q.get("title") or "").strip()
            options = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
            if not stem or not options:
                continue
            index = q.get("index")
            qid = hashlib.sha1(f"{rel}#{index}".encode("utf-8")).hexdigest()
            yield {
                "id": qid,
                "subject": subject,
                "file": file_title,
                "index": index,
                "stem": stem,
                "options": options,
                "answer": str(q.get("answer") or "").strip(),
                "analysis": str(q.get("analysis") or "").strip(),
                # 检索文本：题面语义（答案/解析不进向量，避免按答案措辞误召回）
                "text": f"{subject} | {file_title} | {stem} | {' '.join(options)}",
            }


def embed_batch(texts: List[str], *, url: str, model: str, api_key: str) -> List[List[float]]:
    body = json.dumps({"input": texts, "model": model}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return [item["embedding"] for item in payload["data"]]


def main() -> int:
    parser = argparse.ArgumentParser(description="题库全量嵌入 Chroma")
    parser.add_argument("--bank-dir", default=str(DEFAULT_BANK))
    parser.add_argument("--collection", default=COLLECTION)
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 题（验证用）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    bank_dir = Path(args.bank_dir)
    if not (bank_dir / "data" / "subjects").is_dir():
        print(f"题库目录不存在: {bank_dir}/data/subjects（先克隆数据集）")
        return 1

    t0 = time.time()
    records = iter_questions(bank_dir)
    if args.limit:
        limited: List[Dict[str, Any]] = []
        for rec in records:
            limited.append(rec)
            if len(limited) >= args.limit:
                break
        records = iter(limited)

    if args.dry_run:
        n = 0
        subjects = set()
        for rec in records:
            n += 1
            subjects.add(rec["subject"])
        print(f"[dry-run] 共 {n} 题 / {len(subjects)} 学科（{time.time() - t0:.1f}s）")
        return 0

    _load_env()
    embed_url = os.environ.get("EMBEDDING_BASE_URL", "").strip()
    embed_model = os.environ.get("EMBEDDING_MODEL", "bge-m3").strip()
    embed_key = os.environ.get("EMBEDDING_API_KEY", "local").strip()
    chroma_dir = os.environ.get("CHROMA_DIR") or str(ROOT / "data" / "chroma")
    if not embed_url:
        print("缺少 EMBEDDING_BASE_URL（本地 embedding 端点）")
        return 1

    import chromadb

    client = chromadb.PersistentClient(path=chroma_dir)
    collection = client.get_or_create_collection(
        name=args.collection, metadata={"hnsw:space": "cosine"}
    )
    existing = set(collection.get(include=[])["ids"])  # 只取 id，断点续跑
    print(f"集合 {args.collection} 已有 {len(existing)} 条，开始扫描题库…")

    pending: List[Dict[str, Any]] = []
    skipped = embedded = 0
    last = time.time()

    def flush(batch: List[Dict[str, Any]]) -> None:
        nonlocal embedded
        vectors = embed_batch(
            [r["text"] for r in batch], url=embed_url, model=embed_model, api_key=embed_key
        )
        collection.add(
            ids=[r["id"] for r in batch],
            embeddings=vectors,
            documents=[r["text"] for r in batch],
            metadatas=[
                {
                    "subject": r["subject"],
                    "file": r["file"],
                    "index": r["index"] if r["index"] is not None else -1,
                    "stem": r["stem"],
                    "options": json.dumps(r["options"], ensure_ascii=False),
                    "answer": r["answer"],
                    "analysis": r["analysis"],
                }
                for r in batch
            ],
        )
        embedded += len(batch)

    buffer: List[Dict[str, Any]] = []
    for rec in records:
        if rec["id"] in existing:
            skipped += 1
            continue
        buffer.append(rec)
        if len(buffer) >= EMBED_BATCH:
            flush(buffer)
            buffer = []
            now = time.time()
            if now - last >= 15:
                print(f"  … 已嵌入 {embedded} 题（跳过已有 {skipped}，{now - t0:.0f}s）")
                last = now
    if buffer:
        flush(buffer)

    print(
        f"\n导入完成（{time.time() - t0:.0f}s）：本次嵌入 {embedded} 题，"
        f"跳过已有 {skipped} 题，集合合计 {collection.count()} 条"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
