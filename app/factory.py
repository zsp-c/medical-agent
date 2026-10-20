"""装配工厂。

这是唯一知道「各模块如何拼装」的地方：其他模块之间互不 import，
新增能力只需在 `build_agent` 里多接一步。这里**不含业务逻辑**。

对外暴露两组入口：
- `build_agent(settings)` / `close(built)`：单 Agent 路径（示例与自测用）
- `AgentPool`：**BYOK 多配置路径**（生产用）——按请求里的 `LLMConfig`
  缓存装配结果，用户 key 只在内存中使用、不持久化
"""

import asyncio
import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional, Sequence

from openjiuwen.core.foundation.tool import LocalFunction
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard, ReActAgent
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperationCard,
)

from app.memory.setup import build_memory_engine
from app.repositories.conversation_store import (
    ConversationStore,
    SqliteConversationStore,
)
from app.repositories.graph_repository import GraphRepository
from app.repositories.translation_cache import (
    SqliteTranslationCache,
    TranslationCache,
)
from app.schemas import ChatRequest, ChatResponse, LLMConfig
from app.services.agent_service import AgentService
from app.services.context_service import ContextService
from app.services.graph_query_service import (
    GraphQueryService,
    build_graph_repository,
)
from app.services.memory_service import MemoryEngine, MemoryService
from app.services.translation_service import TranslationService
from app.settings import Settings, build_react_config, quiet_logs
from app.throttle import RateGate
from app.tools.graph_tools import make_graph_tools
from app.tools.translation_tools import make_translation_tool

logger = logging.getLogger(__name__)

AGENT_NAME = "medical-kg-agent"
AGENT_DESCRIPTION = "中文医疗知识图谱问答助手：图谱工具检索 + 短期上下文 + 长期记忆"


class ThrottleRail(AgentRail):
    """把共享节流闸门挂到 Agent 的每次模型调用前。"""

    def __init__(self, gate: RateGate) -> None:
        super().__init__()
        self._gate = gate

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        await self._gate.acquire()


@dataclass
class BuiltAgent:
    """装配结果：Agent 本体 + 各协作服务，`service` 是后端唯一调用点。"""

    agent: ReActAgent
    settings: Settings
    store: ConversationStore
    graph: GraphQueryService
    context: ContextService
    memory: MemoryService
    translation: TranslationService
    service: AgentService


async def build_agent(
    settings: Settings,
    *,
    store: Optional[ConversationStore] = None,
    memory_engine: Optional[MemoryEngine] = None,
    translation_cache: Optional[TranslationCache] = None,
    graph_repo: Optional[GraphRepository] = None,
    enable_memory: bool = True,
    enable_translation: bool = True,
    extra_tools: Sequence[LocalFunction] = (),
) -> BuiltAgent:
    """组装一个可直接对话的 Agent。

    Args:
        settings: 从环境变量加载的配置（BYOK 池会 replace 出每配置副本）
        store: 会话存储；缺省用 SQLite 实现（后端换 PG 时从外部注入）
        memory_engine: 长期记忆引擎；缺省由 `app.memory.setup` 用内置引擎装配
        translation_cache: 翻译缓存；缺省用 SQLite 实现
        graph_repo: 共享图谱仓储（BYOK 池注入，Agent 池共用一个 Neo4j 连接）；
            传入后本 Agent 的 GraphQueryService 不负责释放它
        enable_memory: 置 False 则完全不装记忆（`MemoryService` 自动降级为无记忆）
        enable_translation: 置 False 则不注册翻译工具
        extra_tools: 额外工具
    """
    quiet_logs()

    agent = ReActAgent(card=AgentCard(name=AGENT_NAME, description=AGENT_DESCRIPTION))

    # 技能手册通过 read_file 按需加载，而 SDK 不会自动提供该工具：
    # 必须建一个本地文件操作卡、把 read_file 注入 AbilityManager，否则技能静默失效。
    sysop_card = SysOperationCard(mode=OperationMode.LOCAL, work_config=LocalWorkConfig())
    Runner.resource_mgr.add_sys_operation(sysop_card)
    agent.configure(build_react_config(settings, sys_operation_id=sysop_card.id))
    read_file_card = Runner.resource_mgr.get_sys_op_tool_cards(
        sys_operation_id=sysop_card.id, operation_name="fs", tool_name="read_file"
    )
    agent.ability_manager.add([read_file_card])

    # Agent 推理、记忆抽取、翻译共用同一闸门，按 RPM 上限统一限流
    gate = RateGate(settings.llm_rpm_limit)

    # 图谱工具：模型只选工具 + 填参数，Cypher 由 GraphQueryService 参数化生成。
    # add_ability(card, resource) 是官方统一注册入口。
    graph = GraphQueryService(settings, repo=graph_repo)
    translation = TranslationService(
        translation_cache or SqliteTranslationCache(settings.sqlite_path),
        settings=settings,
        gate=gate,
    )
    tools: list[LocalFunction] = [*make_graph_tools(graph)]
    if enable_translation:
        tools.append(make_translation_tool(translation))
    tools.extend(extra_tools)
    for item in tools:
        agent.ability_manager.add_ability(item.card, item)

    # 技能：只有名称与描述进系统提示词，正文由模型需要时用 read_file 按需加载
    skills_dir = Path(settings.skills_dir)
    if not skills_dir.is_dir():
        raise FileNotFoundError(f"技能目录不存在：{skills_dir}")
    await agent.register_skill(str(skills_dir))

    await agent.register_rail(ThrottleRail(gate))

    if enable_memory and memory_engine is None:
        memory_engine = await build_memory_engine(settings, gate=gate)

    store = store or SqliteConversationStore(settings.sqlite_path)
    context = ContextService(store, settings.context_window_rounds)
    memory = MemoryService(settings.memory_scope_id, engine=memory_engine)

    return BuiltAgent(
        agent=agent,
        settings=settings,
        store=store,
        graph=graph,
        context=context,
        memory=memory,
        translation=translation,
        service=AgentService(agent, context, graph, memory),
    )


async def close(built: Optional[BuiltAgent] = None) -> None:
    """释放资源。

    必须显式调用：未释放的 SDK 资源会在进程退出时被 GC 在其它 task 里回收，
    产生 anyio 的 cancel scope 报错。
    """
    if built is not None:
        # 先等后台记忆沉淀跑完，再释放它正在使用的记忆引擎（见 agent_service.drain_background）
        await built.service.drain_background()
        built.graph.close()
        built.store.close()
        built.translation.close()
        await built.memory.close()
    await Runner.resource_mgr.release()


__all__ = ["AgentPool", "BuiltAgent", "build_agent", "close", "ThrottleRail"]


class AgentPool:
    """BYOK 装配池：按 (base_url, model, api_key) 缓存装配结果，是生产的唯一入口。

    语义与约束：
    - **用户 key 只在内存中使用**：哈希后做缓存键，不写 SQLite / Chroma / 日志 / meta，
      不落任何持久层；进程退出即消失（前端把 key 保存在浏览器侧，每次请求随 body 传入）。
    - 不同 LLM 配置各自独立装配：各自的 RateGate（RPM 按 key 隔离）、各自的记忆抽取模型。
    - 图谱仓储 / 会话库 / 翻译缓存是**共享基础设施**（与模型无关），全池只建一份。
    - 装配经全局锁串行：`Runner.resource_mgr` 是全局单例，并发装配会互相踩。
    - LRU 上限 `max_size`：被逐出的 Agent 只释放记忆引擎，**不**调
      `Runner.resource_mgr.release()`（全局方法，会拆掉仍在使用的其它 Agent 的资源）；
      其 SDK 侧资源随进程退出统一释放（已知限制，见项目构建细节文档）。
    """

    def __init__(self, base_settings: Settings, max_size: int = 8) -> None:
        self._base = base_settings
        self._max_size = max_size
        self._agents: "OrderedDict[str, BuiltAgent]" = OrderedDict()
        self._locks: Dict[str, asyncio.Lock] = {}
        self._build_lock = asyncio.Lock()
        # 共享基础设施（惰性创建）
        self._store: Optional[ConversationStore] = None
        self._graph_repo: Optional[GraphRepository] = None
        self._translation_cache: Optional[TranslationCache] = None

    # ---- 缓存键：只留哈希，不留 key 本体 ----
    @staticmethod
    def _cache_key(llm: Optional[LLMConfig]) -> str:
        if llm is None:
            return "default"
        raw = f"{llm.base_url}|{llm.model}|{llm.api_key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _ensure_shared(self) -> None:
        if self._store is None:
            self._store = SqliteConversationStore(self._base.sqlite_path)
            self._graph_repo = build_graph_repository(self._base)
            self._translation_cache = SqliteTranslationCache(self._base.sqlite_path)

    async def _get(self, llm: Optional[LLMConfig]) -> BuiltAgent:
        key = self._cache_key(llm)
        built = self._agents.get(key)
        if built is not None:
            self._agents.move_to_end(key)
            return built
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:  # 同一配置的并发请求只装配一次
            built = self._agents.get(key)
            if built is None:
                settings = self._base if llm is None else replace(
                    self._base,
                    api_key=llm.api_key,
                    base_url=llm.base_url,
                    model=llm.model,
                )
                self._ensure_shared()
                async with self._build_lock:  # resource_mgr 全局单例，装配串行
                    built = await build_agent(
                        settings,
                        store=self._store,
                        graph_repo=self._graph_repo,
                        translation_cache=self._translation_cache,
                    )
                self._agents[key] = built
                if len(self._agents) > self._max_size:
                    _, evicted = self._agents.popitem(last=False)
                    await self._evict(evicted)
                logger.info("BYOK 装配新配置: cache_key=%s model=%s", key, settings.model)
            return built

    @staticmethod
    async def _evict(built: BuiltAgent) -> None:
        try:
            await built.service.drain_background()
        except Exception:
            logger.warning("逐出 Agent 时等待后台沉淀失败", exc_info=True)
        try:
            await built.memory.close()
        except Exception:
            logger.warning("逐出 Agent 时释放记忆引擎失败", exc_info=True)

    async def handle_chat(self, req: ChatRequest) -> ChatResponse:
        """生产入口：按 req.llm 取（或装配）Agent 并转发。req.llm=None 走服务端默认配置。"""
        built = await self._get(req.llm)
        return await built.service.handle_chat(req)

    async def handle_chat_stream(self, req: ChatRequest) -> AsyncIterator[Dict[str, Any]]:
        """流式生产入口：同 handle_chat，事件流由后端映射成 SSE。"""
        built = await self._get(req.llm)
        async for event in built.service.handle_chat_stream(req):
            yield event

    async def close(self) -> None:
        """进程退出时调用：等所有后台沉淀、释放共享设施与 SDK 全局资源。"""
        for built in list(self._agents.values()):
            try:
                await built.service.drain_background()
                await built.memory.close()
            except Exception:
                logger.warning("关闭池内 Agent 失败", exc_info=True)
        self._agents.clear()
        if self._graph_repo is not None:
            self._graph_repo.close()
        if self._store is not None:
            self._store.close()
        if self._translation_cache is not None:
            self._translation_cache.close()
        await Runner.resource_mgr.release()
