"""端到端示例一：单轮问答（图谱工具 + 证据 + 长期记忆）。

跑之前请确认：
- `.env` 已按 `.env.example` 填好
- Neo4j 已起且已导入样例数据（`scripts/import_sample.py`）；没起也行，会自动降级到 CSV 样例

注意：LLM 调用按 `LLM_RPM_LIMIT`（默认 100/分钟）共享限流，
正常使用不会因节流等待；一轮问答含记忆抽取的耗时主要来自模型自身生成。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ChatRequest, Settings, build_agent, close  # noqa: E402


async def main() -> None:
    settings = Settings.load()
    built = await build_agent(settings)
    print(f"图谱后端={built.graph.backend}  长期记忆={'开' if built.memory.enabled else '关'}")

    req = ChatRequest(
        user_id="demo-user",
        session_id="demo-session-1",
        query="高血压有哪些相关的疾病？请给出依据。",
        request_id="demo-req-1",
    )
    resp = await built.service.handle_chat(req)

    print("\n=== 回答 ===")
    print(resp.answer)
    print("\n=== 证据实体（前端可跳 /api/entities/{id}）===")
    for item in resp.evidence_entities:
        print(f"- {item.entity_key}  {item.name}  ({item.entity_type})")
    print("\n=== 运行态 ===")
    for key, value in resp.meta.items():
        print(f"- {key}: {value}")

    await close(built)


if __name__ == "__main__":
    asyncio.run(main())
