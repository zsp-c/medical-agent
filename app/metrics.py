"""单轮问答的分阶段耗时与 token 采集。

`MetricsRail` 挂在 Agent 上，在 before/after 钩子中记录：

- 模型调用：ReAct 迭代号、耗时、prompt/completion token（部分流式响应无 usage 时记 None）
- 工具调用：名称、耗时、返回体字符数（衡量注入上下文的体积）

用法与 `GraphQueryService.begin_round` 相同：AgentService 每轮开始清零，
结束后 `snapshot()` 取数写进 meta 与日志。前提同样是同一 Agent 实例的请求串行进入。
"""

import time
from typing import Any, Dict, List, Optional, Tuple

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail


def _usage_tokens(response: Any) -> Tuple[Optional[int], Optional[int]]:
    """从不同厂商的响应对象上尽力取 (prompt_tokens, completion_tokens)。"""
    usage = getattr(response, "usage", None) or getattr(response, "usage_metadata", None)
    if usage is None:
        return None, None
    prompt = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None)
    completion = getattr(usage, "completion_tokens", None) or getattr(
        usage, "output_tokens", None
    )
    return prompt, completion


class MetricsRail(AgentRail):
    """采集单轮内每次模型/工具调用的耗时、token 与工具返回体积。"""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self.model_calls: List[Dict[str, Any]] = []
        self.tool_calls: List[Dict[str, Any]] = []
        self._model_started: Optional[float] = None
        # 并行工具调用各自有独立的 ToolCallInputs，按其实例 id 配对 before/after
        self._tool_started: Dict[int, Tuple[float, str]] = {}

    def begin_round(self) -> None:
        self._reset()

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        self._model_started = time.perf_counter()

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        started = self._model_started
        self._model_started = None
        elapsed_ms = (
            round((time.perf_counter() - started) * 1000) if started else None
        )
        prompt, completion = _usage_tokens(getattr(ctx.inputs, "response", None))
        self.model_calls.append(
            {
                "iteration": getattr(ctx.inputs, "react_iteration", 0),
                "elapsed_ms": elapsed_ms,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
            }
        )

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        self._tool_started[id(ctx.inputs)] = (
            time.perf_counter(),
            getattr(ctx.inputs, "tool_name", ""),
        )

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        entry = self._tool_started.pop(id(ctx.inputs), None)
        started, name = entry if entry else (None, getattr(ctx.inputs, "tool_name", ""))
        elapsed_ms = (
            round((time.perf_counter() - started) * 1000) if started else None
        )
        result = getattr(ctx.inputs, "tool_result", None)
        result_chars = (
            len(result)
            if isinstance(result, str)
            else len(str(result))
            if result is not None
            else 0
        )
        self.tool_calls.append(
            {"name": name, "elapsed_ms": elapsed_ms, "result_chars": result_chars}
        )

    def snapshot(self) -> Dict[str, Any]:
        """汇总本轮数据；usage 缺失的调用按 0 计入合计。"""
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "model_call_count": len(self.model_calls),
            "prompt_tokens_total": sum(
                c["prompt_tokens"] or 0 for c in self.model_calls
            ),
            "completion_tokens_total": sum(
                c["completion_tokens"] or 0 for c in self.model_calls
            ),
        }


__all__ = ["MetricsRail"]
