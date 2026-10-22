"""单轮对话总编排（策划书 6.4 / 构建细节 §4 的落地）。

顺序约束：**校验归属 → 读上下文 → 检索记忆 → 推理 + 工具 → 写回 → 沉淀**。
检索不消耗对话模型配额（只花 embedding），沉淀与推理才消耗，所以顺序不能乱。

第 6 步（沉淀）在响应返回之后以后台任务执行：它固定要 2~3 次模型调用（实测占单轮约 40%
耗时），同步等待只会让用户白等十几秒。释放资源前必须用 `drain_background()` 把它等完。

后端只需调用 `AgentService.handle_chat(req) -> ChatResponse`。
"""

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

from openjiuwen.core.runner import Runner

from app.schemas import ChatRequest, ChatResponse, EntityBrief, MemoryHit, Message
from app.services.context_service import ContextService
from app.services.graph_query_service import GraphQueryService
from app.services.memory_service import MemoryService

logger = logging.getLogger(__name__)


def compose_query(
    query: str,
    hits: List[MemoryHit],
    files: Sequence[Dict[str, str]] = (),
) -> str:
    """把长期记忆与上传文件正文拼进本轮 query。两者皆无命中时原样返回，不污染输入。"""
    sections: List[str] = []
    memory_lines = "\n".join(f"- {hit.content}" for hit in hits if hit.content)
    if memory_lines:
        sections.append(f"[长期记忆（来自历史会话的沉淀，仅供参考，可能过时）]\n{memory_lines}")
    for block in files:
        sections.append(f"[用户上传的文件《{block['name']}》内容]\n{block['text']}")
    if not sections:
        return query
    return "\n\n".join(sections) + f"\n\n[当前问题]\n{query}"


def extract_text(result: Any) -> str:
    """从 run_agent 的返回结构里取出最终文本。"""
    output = result.get("output") if isinstance(result, dict) else result
    if isinstance(output, str):
        return output
    content = getattr(output, "content", None)
    if isinstance(content, str):
        return content
    return str(output)


class AgentService:
    """后端唯一入口。装配用的协作者由 `app.factory` 注入。"""

    def __init__(
        self,
        agent: Any,
        context: ContextService,
        graph: GraphQueryService,
        memory: MemoryService,
        questions: Optional[Any] = None,
        files: Optional[Any] = None,
        metrics: Optional[Any] = None,
    ) -> None:
        self._agent = agent
        self._context = context
        self._graph = graph
        self._memory = memory
        # 题库服务（search_questions / present_quiz 的工具事件与题卡收集器）
        self._questions = questions
        # 上传文件服务（按 attachments 注入文件正文，缺省不启用）
        self._files = files
        # 分阶段耗时/token 采集器（缺省时只保留总耗时）
        self._metrics = metrics
        # 后台沉淀的任务与串行锁：同一 (user_id, session_id) 不允许并发写记忆
        self._bg_tasks: set[asyncio.Task] = set()
        self._bg_keys: Dict[asyncio.Task, str] = {}
        self._bg_locks: Dict[str, asyncio.Lock] = {}

    def _load_files(self, req: ChatRequest) -> List[Dict[str, str]]:
        """读取本轮携带的上传文件正文（归属与就绪状态校验在 FileService 内）。"""
        if self._files is None or not req.attachments:
            return []
        return self._files.load_texts(req.user_id, req.attachments)

    # ---- 分阶段耗时/token -------------------------------------------------

    def _timing_meta(
        self, context_ms: int, memory_ms: int, runner_ms: int
    ) -> Dict[str, Any]:
        """组装 meta["timing"]：编排各阶段耗时 + 本轮模型/工具调用明细。"""
        timing: Dict[str, Any] = {
            "context_ms": context_ms,
            "memory_ms": memory_ms,
            "runner_ms": runner_ms,
        }
        if self._metrics is not None:
            timing.update(self._metrics.snapshot())
        return timing

    def _log_timing(self, req: ChatRequest, meta: Dict[str, Any]) -> None:
        """把本轮耗时与 token 汇总写进服务端日志（不含回答内容）。"""
        timing = meta["timing"]
        tools = ",".join(
            f"{c['name']}:{c['elapsed_ms']}ms" for c in timing.get("tool_calls", [])
        )
        logger.info(
            "chat timing: user=%s session=%s total=%dms context=%dms memory=%dms "
            "runner=%dms model_calls=%d prompt_tok=%s completion_tok=%s tools=[%s]",
            req.user_id,
            req.session_id,
            meta["elapsed_ms"],
            timing["context_ms"],
            timing["memory_ms"],
            timing["runner_ms"],
            timing.get("model_call_count", 0),
            timing.get("prompt_tokens_total"),
            timing.get("completion_tokens_total"),
            tools,
        )

    async def handle_chat(self, req: ChatRequest) -> ChatResponse:
        started = time.perf_counter()

        # 1 校验会话归属（不属于该 user_id 时抛 SessionOwnershipError，由后端映射成 403）
        self._context.ensure_session(req.user_id, req.session_id)

        # 2 读近期消息：既是归档的完整历史，也是长期记忆沉淀的素材
        t = time.perf_counter()
        recent: List[Message] = self._context.load_recent(req.user_id, req.session_id)
        context_ms = round((time.perf_counter() - t) * 1000)

        # 3 检索长期记忆（失败降级为空，不阻断对话）+ 读取上传文件正文
        t = time.perf_counter()
        hits = await self._memory.retrieve(req.user_id, req.query)
        file_blocks = self._load_files(req)
        memory_ms = round((time.perf_counter() - t) * 1000)

        # 4 推理 + 工具；模型按需调用白名单图谱/题库工具，检索细节全在服务层
        self._graph.begin_round()
        if self._questions is not None:
            self._questions.begin_round()
        if self._metrics is not None:
            self._metrics.begin_round()
        t = time.perf_counter()
        result = await Runner.run_agent(
            agent=self._agent,
            inputs={
                "query": compose_query(req.query, hits, file_blocks),
                "conversation_id": req.session_id,
            },
        )
        runner_ms = round((time.perf_counter() - t) * 1000)
        answer = extract_text(result)
        evidence: List[EntityBrief] = self._graph.evidence

        # 5 写回消息（request_id 相同则幂等跳过）
        written = self._context.append_turn(
            req.user_id, req.session_id, req.query, answer, req.request_id
        )

        # 6 沉淀长期记忆：排进后台任务，响应不再等它（失败只记日志，不回滚已写入的消息）
        # 只沉淀本轮新消息：记忆引擎内部自己有消息归档（gen_mem_with_history_msg_num），
        # 把 recent 一起塞进去会让历史轮次被重复抽取、写入重复记忆。
        turn_messages = [
            Message(
                session_id=req.session_id, user_id=req.user_id, role="user", content=req.query
            ),
            Message(
                session_id=req.session_id, user_id=req.user_id, role="assistant", content=answer
            ),
        ]
        memory_scheduled = self._schedule_remember(
            req.user_id, req.session_id, turn_messages
        )

        meta: Dict[str, Any] = {
            "graph_backend": self._graph.backend,
            "memory_enabled": self._memory.enabled,
            "memory_used": len(hits),
            # 沉淀在响应之后异步进行，故这里只报告是否已排入后台，不报告结果
            "memory_scheduled": memory_scheduled,
            "history_count": len(recent),
            "messages_written": written,
            "files_used": len(file_blocks),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
        meta["timing"] = self._timing_meta(context_ms, memory_ms, runner_ms)
        self._log_timing(req, meta)
        # 本轮若调用了 present_quiz，把题卡随响应带回（前端据此渲染互动题卡）
        if self._questions is not None:
            quiz = self._questions.pop_quiz()
            if quiz:
                meta["quiz"] = quiz
        return ChatResponse(
            answer=answer,
            request_id=req.request_id,
            evidence_entities=evidence,
            memory_used=hits,
            meta=meta,
        )

    async def handle_chat_stream(self, req: ChatRequest) -> AsyncIterator[Dict[str, Any]]:
        """流式入口：与 handle_chat 同一编排，仅把"等最终结果"改为"边推理边推事件"。

        事件序列（dict，由后端映射成 SSE）：
        - {"event": "tool_call", "name": ...}        模型发起一次工具调用
        - {"event": "tool_result", "name": ...}      工具返回（不带结果体，避免刷屏）
        - {"event": "quiz", "stem"/"options"/...}    present_quiz 推送的互动题卡（无答案）
        - {"event": "delta", "text": ...}            最终回答的增量文本（llm_output）
        - {"event": "done", "response": {...}}       结束，含完整 ChatResponse
        模型的思考过程（llm_reasoning）不透出——它不是回答的一部分。
        """
        started = time.perf_counter()

        # 1~3 与非流式一致：归属校验 → 短期上下文 → 长期记忆检索 + 上传文件正文
        self._context.ensure_session(req.user_id, req.session_id)
        t = time.perf_counter()
        recent: List[Message] = self._context.load_recent(req.user_id, req.session_id)
        context_ms = round((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        hits = await self._memory.retrieve(req.user_id, req.query)
        file_blocks = self._load_files(req)
        memory_ms = round((time.perf_counter() - t) * 1000)

        # 4 流式推理 + 工具
        # 工具调用发生在等待 Runner 下一个 chunk 的间隙里（同步钩子无法直接 yield），
        # 因此用队列合并两路事件：Runner chunk 泵 + 工具事件钩子。
        self._graph.begin_round()
        if self._questions is not None:
            self._questions.begin_round()
        if self._metrics is not None:
            self._metrics.begin_round()
        t_runner = time.perf_counter()
        queue: asyncio.Queue = asyncio.Queue()
        pump_done = object()

        async def _pump() -> None:
            try:
                async for chunk in Runner.run_agent_streaming(
                    agent=self._agent,
                    inputs={
                        "query": compose_query(req.query, hits, file_blocks),
                        "conversation_id": req.session_id,
                    },
                ):
                    queue.put_nowait(("chunk", chunk))
            except Exception as exc:  # 流式中途失败也要让消费端退出
                queue.put_nowait(("error", exc))
            finally:
                queue.put_nowait(("end", pump_done))

        tool_hook = lambda name, phase: queue.put_nowait(("tool", (name, phase)))  # noqa: E731
        self._graph.set_tool_hook(tool_hook)
        if self._questions is not None:
            self._questions.set_tool_hook(tool_hook)
        task = asyncio.create_task(_pump())
        parts: List[str] = []
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "end":
                    break
                if kind == "error":
                    raise payload
                if kind == "tool":
                    name, phase = payload
                    yield {
                        "event": "tool_call" if phase == "start" else "tool_result",
                        "name": name,
                    }
                    # present_quiz 结束即把题卡推给前端（答案不下发，用户作答后由模型判题）
                    if (
                        name == "present_quiz"
                        and phase == "end"
                        and self._questions is not None
                    ):
                        quiz = self._questions.pop_quiz()
                        if quiz:
                            yield {"event": "quiz", **quiz}
                    continue
                ctype = getattr(payload, "type", "")
                cpayload = getattr(payload, "payload", None) or {}
                if ctype == "llm_output":
                    text = cpayload.get("content", "")
                    if text:
                        parts.append(text)
                        yield {"event": "delta", "text": text}
        finally:
            self._graph.set_tool_hook(None)
            if self._questions is not None:
                self._questions.set_tool_hook(None)
            if not task.done():
                task.cancel()
        runner_ms = round((time.perf_counter() - t_runner) * 1000)
        answer = "".join(parts)
        evidence: List[EntityBrief] = self._graph.evidence

        # 5~6 与非流式一致：写回（幂等）→ 后台沉淀
        written = self._context.append_turn(
            req.user_id, req.session_id, req.query, answer, req.request_id
        )
        turn_messages = [
            Message(session_id=req.session_id, user_id=req.user_id, role="user", content=req.query),
            Message(session_id=req.session_id, user_id=req.user_id, role="assistant", content=answer),
        ]
        memory_scheduled = self._schedule_remember(req.user_id, req.session_id, turn_messages)

        meta: Dict[str, Any] = {
            "graph_backend": self._graph.backend,
            "memory_enabled": self._memory.enabled,
            "memory_used": len(hits),
            "memory_scheduled": memory_scheduled,
            "history_count": len(recent),
            "messages_written": written,
            "files_used": len(file_blocks),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
        meta["timing"] = self._timing_meta(context_ms, memory_ms, runner_ms)
        self._log_timing(req, meta)
        yield {
            "event": "done",
            "response": ChatResponse(
                answer=answer,
                request_id=req.request_id,
                evidence_entities=evidence,
                memory_used=hits,
                meta=meta,
            ).model_dump(),
        }

    # ---- 后台记忆沉淀 --------------------------------------------------------

    def _schedule_remember(
        self, user_id: str, session_id: str, messages: Sequence[Message]
    ) -> bool:
        """把本轮沉淀排进后台任务；返回是否真的排上了。"""
        if not self._memory.enabled or not messages:
            return False
        key = f"{user_id}|{session_id}"
        task = asyncio.create_task(
            self._serialized_remember(key, user_id, session_id, messages)
        )
        self._bg_tasks.add(task)
        self._bg_keys[task] = key
        task.add_done_callback(self._on_bg_done)
        return True

    async def _serialized_remember(
        self, key: str, user_id: str, session_id: str, messages: Sequence[Message]
    ) -> None:
        """按 (user_id, session_id) 串行沉淀，避免同一会话连续两轮并发写记忆。"""
        lock = self._bg_locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                await self._memory.remember(user_id, session_id, messages)
        except Exception:
            logger.warning("后台记忆沉淀失败，已忽略", exc_info=True)

    def _on_bg_done(self, task: asyncio.Task) -> None:
        """任务结束后回收，同一 key 没有别的待处理任务时释放锁。"""
        self._bg_tasks.discard(task)
        key = self._bg_keys.pop(task, None)
        if key is not None and key not in self._bg_keys.values():
            self._bg_locks.pop(key, None)

    async def drain_background(self) -> None:
        """等所有后台沉淀跑完。

        `app.factory.close()` 必须先调用它再释放记忆引擎：否则引擎可能在后台任务仍在使用时
        被拆掉（既定教训：未释放的 SDK 资源被 GC 在别的 task 里回收会触发 anyio 报错）。

        注意这也意味着**后台沉淀未完成时，同一 user 的新一轮提问召不回本条记忆**；
        需要严格"写完再问"的场景（如 `examples/02_memory.py` 的跨会话演示）应显式调用它。
        """
        if self._bg_tasks:
            await asyncio.gather(*list(self._bg_tasks), return_exceptions=True)
