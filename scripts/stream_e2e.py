"""流式端到端验证：触发工具调用的问题，收集全部事件并校验完整性。"""
import asyncio
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.factory import AgentPool  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402
from app.settings import Settings  # noqa: E402


async def main() -> None:
    pool = AgentPool(Settings.load())
    events: list[dict] = []
    try:
        t0 = time.perf_counter()
        async for ev in pool.handle_chat_stream(
            ChatRequest(
                user_id="u_stream2",
                session_id="s_stream_e2e_v2",
                query="氨氯地平有什么副作用？",
                request_id=f"stream-{uuid.uuid4().hex[:8]}",
            )
        ):
            events.append(ev)
            kind = ev["event"]
            if kind == "tool_call":
                print(f"  [tool_call] {ev.get('name')}", flush=True)
            elif kind == "tool_result":
                print(f"  [tool_result] {ev.get('name')}", flush=True)
        total = (time.perf_counter() - t0) * 1000

        counts = Counter(e["event"] for e in events)
        deltas = [e for e in events if e["event"] == "delta"]
        done = [e for e in events if e["event"] == "done"]
        assembled = "".join(e["text"] for e in deltas)
        final = done[0]["response"] if done else {}

        print(f"\n总耗时 {total:.0f} ms")
        print(f"事件统计: {dict(counts)}")
        print(f"delta 拼接长度 {len(assembled)}  done.answer 长度 {len(final.get('answer', ''))}")
        print(f"拼接与 done.answer 一致: {assembled == final.get('answer', '')}")
        print(f"证据实体 {len(final.get('evidence_entities', []))} 个")
        print(f"meta: {final.get('meta')}")
        print(f"\n回答前 200 字:\n{final.get('answer', '')[:200]}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
