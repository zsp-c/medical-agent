"""LLM 调用节流（全项目共享）。

厂商按 RPM（每分钟请求数）限额，而一次提问最坏会触发多次模型调用：Agent 的 ReAct 迭代、
长期记忆抽取（一次 `add_messages` 内部还会连发多次）、按需翻译。三者走的是不同的
`Model` 实例，各自限流等于没有限流 —— 必须由**同一个**闸门来统计全项目的真实请求。

策略是**滑动窗口计数**，而不是固定最小间隔：正常使用下不加任何延迟，
只有最近 60 秒内的请求数达到上限时才等待最早的请求滑出窗口。

- `RateGate`：进程级共享闸门，`acquire()` 在额度内立即放行，超限时等待
- `ThrottledModel`：把闸门套在每次 `invoke` 上的 Model 子类
"""

import asyncio
import time
from collections import deque
from typing import Any, Deque, Optional

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)

_WINDOW_SECONDS = 60.0


class RateGate:
    """共享节流闸门：滑动窗口 RPM 限流。`rpm<=0` 即关闭节流（测试用）。"""

    def __init__(self, rpm: int, window: float = _WINDOW_SECONDS) -> None:
        self._rpm = max(0, int(rpm))
        self._window = float(window)
        self._lock = asyncio.Lock()
        self._recent: Deque[float] = deque()

    @property
    def rpm(self) -> int:
        return self._rpm

    async def acquire(self) -> None:
        if self._rpm <= 0:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                # 丢弃已滑出窗口的请求时间戳
                while self._recent and now - self._recent[0] >= self._window:
                    self._recent.popleft()
                if len(self._recent) < self._rpm:
                    self._recent.append(now)
                    return
                # 额度用尽：等到最早那次请求滑出窗口再争抢
                await asyncio.sleep(max(self._window - (now - self._recent[0]), 0.001))


class ThrottledModel(Model):
    """每次真实请求都过闸门的 Model。"""

    def __init__(
        self,
        model_client_config: ModelClientConfig,
        model_config: Optional[ModelRequestConfig] = None,
        *,
        gate: Optional[RateGate] = None,
    ) -> None:
        super().__init__(
            model_client_config=model_client_config, model_config=model_config
        )
        self._gate = gate

    async def invoke(self, *args: Any, **kwargs: Any) -> Any:
        if self._gate is not None:
            await self._gate.acquire()
        return await super().invoke(*args, **kwargs)

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        """流式请求同样计 1 次 RPM，进入迭代前先过闸门。"""
        if self._gate is not None:
            await self._gate.acquire()
        async for chunk in super().stream(*args, **kwargs):
            yield chunk


__all__ = ["RateGate", "ThrottledModel"]
