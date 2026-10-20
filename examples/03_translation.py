"""端到端示例三：按需翻译 + 缓存（命中缓存不产生 LLM 请求）。

第一次调用会真正请求一次模型（同批术语只发一次），之后同样术语直接命中缓存。
`llm_calls` 是进程内累计的真实 LLM 请求次数，用它就能验证"第二次没有请求"。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import Settings, build_agent, close  # noqa: E402

TERMS = ["Hypertension", "Renin-Angiotensin System", "ACE"]


async def main() -> None:
    settings = Settings.load()
    # 本示例不需要记忆，关闭以免多花 LLM 配额
    built = await build_agent(settings, enable_memory=False)
    svc = built.translation

    first = await svc.translate_batch(TERMS, "term")
    print("=== 第一次 ===")
    print(f"本次是否真的请求了模型：{'是' if svc.llm_calls else '否（术语已在缓存中，来自之前运行）'}")
    for src, dst in first.items():
        print(f"- {src} → {dst}")

    before = svc.llm_calls
    second = await svc.translate_batch(TERMS + ["Hypertension"], "term")
    print("\n=== 第二次（应全部命中缓存，不产生 LLM 请求）===")
    print(f"本次是否真的请求了模型：{'是' if svc.llm_calls > before else '否（全部命中缓存）'}")
    for src, dst in second.items():
        print(f"- {src} → {dst}")

    await close(built)


if __name__ == "__main__":
    asyncio.run(main())
