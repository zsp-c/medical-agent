"""端到端示例二：跨会话长期记忆。

关键点：**同一个 user_id、换一个全新的 session_id**，
第二轮提问仍应召回第一轮沉淀的信息 —— 这证明记忆是按 `user_id` 隔离的长期记忆，
而不是只靠短期上下文（第二轮 `history_count` 应为 0）。

注意：LLM 调用按 `LLM_RPM_LIMIT`（默认 100/分钟）共享限流，正常使用不会因节流等待。
脚本耗时主要来自模型推理与记忆抽取；沉淀在响应之后异步执行，本示例为演示"下一轮就能
召回"而显式等它跑完（`drain_background()`），真实产品可省略那一步。
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ChatRequest, Settings, build_agent, close  # noqa: E402

USER_ID = "demo-memory-user"


async def main() -> None:
    settings = Settings.load()
    built = await build_agent(settings)
    if not built.memory.enabled:
        print("长期记忆未装配，本示例无法演示（检查 .env 与 embedding 配置）")
        await close(built)
        return

    tag = str(int(time.time()))

    # 第一轮：告诉 Agent 一些关于用户的事实（这些会被沉淀成长期记忆）
    first = await built.service.handle_chat(
        ChatRequest(
            user_id=USER_ID,
            session_id=f"demo-memory-{tag}-a",
            query="你好，我是北京大学医学部的学生，主要研究肾素-血管紧张素系统与高血压。",
        )
    )
    print("=== 第一轮（session A）===")
    print(first.answer[:200])
    print(f"历史消息数={first.meta['history_count']}  已排入后台沉淀={first.meta['memory_scheduled']}")

    # 沉淀在响应之后异步进行（实测占单轮约 40% 耗时，故不让用户等）。
    # 本示例要在下一轮就召回它，所以必须显式把它等完；真实产品可省略这一步。
    await built.service.drain_background()
    print("后台记忆沉淀已完成")

    # 第二轮：全新会话，没有任何短期上下文，只能靠长期记忆
    second = await built.service.handle_chat(
        ChatRequest(
            user_id=USER_ID,
            session_id=f"demo-memory-{tag}-b",
            query="我之前说过我主要研究什么系统？",
        )
    )
    print("\n=== 第二轮（新 session B，历史消息数应为 0）===")
    print(second.answer)
    print(f"历史消息数={second.meta['history_count']}  本轮注入记忆条数={second.meta['memory_used']}")
    print("\n=== 本轮用的长期记忆 ===")
    for hit in second.memory_used:
        print(f"- [{hit.kind}] {hit.content}  (score={hit.score:.3f})")

    await close(built)


if __name__ == "__main__":
    asyncio.run(main())
