"""FastAPI 应用：全部 HTTP 接口的装配与路由（接口契约见 接口文档.md）。

启动（项目 venv，需先 `pip install -e .` 装上 fastapi/uvicorn）：

    .\\.venv\\Scripts\\python.exe -m server.main
    # 或
    .\\.venv\\Scripts\\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000

接口一览：
- POST   /api/chat                            非流式问答
- POST   /api/chat/stream                     SSE 流式（tool_call/tool_result/delta/done）
- GET    /api/entities/{entity_key}           实体详情
- GET    /api/entities/{entity_key}/subgraph  局部子图（Cytoscape 数据源）
- DELETE /api/users/me/memory                 清空当前用户长期记忆
- GET    /api/sessions                        会话列表
- GET    /api/sessions/{session_id}/messages  会话历史消息
- DELETE /api/sessions/{session_id}           删除会话
- GET    /api/health                          存活探针（免鉴权）

鉴权：Authorization: Bearer <令牌>，令牌表由 .env 的 API_TOKENS 配置
（"用户名:令牌" 逗号分隔；未配置时兜底 dev-user:dev-token）。
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.factory import AgentPool
from app.schemas import ChatRequest, ChatResponse, GraphResult, LLMConfig
from app.services import SessionOwnershipError
from app.settings import Settings
from server.auth import current_user
from server.config import ServerConfig

logger = logging.getLogger(__name__)

SSE_HEARTBEAT_SECONDS = 20.0  # 静默期注释行心跳，防中间层掐断长连接
_DONE = object()

CurrentUser = Annotated[str, Depends(current_user)]


class ChatBody(BaseModel):
    """HTTP 请求体：与 ChatRequest 字段一致，但不含 user_id（由鉴权注入）。"""

    session_id: str
    query: str
    request_id: str = ""
    llm: Optional[LLMConfig] = None
    options: Dict[str, Any] = Field(default_factory=dict)

    def to_chat_request(self, user_id: str) -> ChatRequest:
        # request_id 缺省时服务端补一个，便于追溯；网络重试要触发幂等仍需前端复用同一值
        return ChatRequest(
            user_id=user_id,
            session_id=self.session_id,
            query=self.query,
            request_id=self.request_id or str(uuid.uuid4()),
            llm=self.llm,
            options=self.options,
        )


def _pool(request: Request) -> AgentPool:
    return request.app.state.pool


def _format_sse(event: Dict[str, Any]) -> str:
    """Agent 事件 dict → SSE 两行（event: / data:）。done 的 data 直接是完整 ChatResponse。"""
    name = event.get("event", "message")
    if name == "done":
        payload = event.get("response") or {}
    else:
        payload = {k: v for k, v in event.items() if k != "event"}
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _sse_stream(pool: AgentPool, req: ChatRequest) -> AsyncIterator[str]:
    """把 pool.handle_chat_stream 映射成 SSE 文本流；静默期发注释行心跳。

    独立任务泵 + 队列合并：直接用 asyncio.wait_for 包 anext 会在超时时把
    正在进行的模型推理一起取消掉，必须让生产端不受心跳超时影响。
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def _pump() -> None:
        try:
            async for event in pool.handle_chat_stream(req):
                await queue.put(event)
        except Exception as exc:  # 连接已建立，中途异常只能转成 error 事件再收尾
            logger.warning("流式对话中断", exc_info=True)
            await queue.put({"event": "error", "detail": str(exc)[:200]})
        finally:
            await queue.put(_DONE)

    task = asyncio.create_task(_pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if item is _DONE:
                return
            yield _format_sse(item)
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


# --------------------------------------------------------------------------- #
# 路由（图谱 / 会话类接口是同步 def：FastAPI 自动放线程池，不阻塞 SSE 事件循环）
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api", tags=["medical-kg-agent"])


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatBody, user: CurrentUser, request: Request) -> ChatResponse:
    """非流式问答。等 Agent 完成全部推理后一次性返回，典型 30~110s。"""
    try:
        return await _pool(request).handle_chat(body.to_chat_request(user))
    except SessionOwnershipError as exc:
        raise HTTPException(status_code=403, detail="无权访问该会话") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"上游模型调用失败：{str(exc)[:200]}"
        ) from exc


@router.post("/chat/stream")
async def chat_stream(body: ChatBody, user: CurrentUser, request: Request) -> StreamingResponse:
    """SSE 流式问答。归属越权（403）在首个事件前以 HTTP 状态返回；中途异常发 error 事件。"""
    pool = _pool(request)
    req = body.to_chat_request(user)
    # 只读预检（不登记会话）：让 403 能以 HTTP 状态码返回，而不是藏在事件流里
    owner = pool.conversation_store().get_session_owner(req.session_id)
    if owner is not None and owner != user:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    return StreamingResponse(
        _sse_stream(pool, req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/entities/{entity_key}")
def entity_details(entity_key: str, user: CurrentUser, request: Request) -> Dict[str, Any]:
    """实体详情。found=false 表示键不存在（200 直通，保持 schema 一致）。"""
    graph = _pool(request).graph_service()
    graph.begin_round()  # 清空证据收集器，避免 HTTP 查询逐次累积
    return graph.get_entity_details(entity_key)


@router.get("/entities/{entity_key}/subgraph", response_model=GraphResult)
def entity_subgraph(
    entity_key: str,
    user: CurrentUser,
    request: Request,
    depth: int = Query(default=1, ge=1, le=2),
    limit: int = Query(default=50, ge=1, le=50),
) -> GraphResult:
    """局部子图（Cytoscape.js 数据源）。上限 depth≤2、limit≤50。"""
    graph = _pool(request).graph_service()
    graph.begin_round()
    return graph.get_subgraph(entity_key, depth, limit)


@router.delete("/users/me/memory")
async def clear_memory(user: CurrentUser, request: Request) -> Dict[str, bool]:
    """清空当前用户的长期记忆（SQLite 与 Chroma 两处一起删）。

    引擎装配失败 / delete_user 失败按契约返回 200 + ok=false，不抛 500。
    """
    try:
        ok = await _pool(request).forget_memory(user)
    except Exception:
        logger.warning("清理用户记忆失败 user=%s", user, exc_info=True)
        ok = False
    return {"ok": ok}


@router.get("/sessions")
def list_sessions(
    user: CurrentUser,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    """当前用户的会话列表（按最近活跃倒序分页）。"""
    store = _pool(request).conversation_store()
    return {
        "sessions": store.list_sessions(user, limit, offset),
        "total": store.count_sessions(user),
    }


def _owned_session(store: Any, session_id: str, user: str) -> None:
    owner = store.get_session_owner(session_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if owner != user:
        raise HTTPException(status_code=403, detail="无权访问该会话")


@router.get("/sessions/{session_id}/messages")
def session_messages(
    session_id: str,
    user: CurrentUser,
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
) -> Dict[str, Any]:
    """会话历史消息（时间正序，取最近 limit 条）。"""
    store = _pool(request).conversation_store()
    _owned_session(store, session_id, user)
    messages = store.load_recent(session_id, limit)
    return {"session_id": session_id, "messages": [m.model_dump() for m in messages]}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, user: CurrentUser, request: Request) -> Dict[str, Any]:
    """删除会话及其全部归档消息（不影响长期记忆）。"""
    store = _pool(request).conversation_store()
    _owned_session(store, session_id, user)
    return {"ok": True, "messages_deleted": store.delete_session(session_id)}


@router.get("/health")
def health() -> Dict[str, str]:
    """存活探针（免鉴权）。"""
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# 应用装配
# --------------------------------------------------------------------------- #
def create_app(
    settings: Optional[Settings] = None, config: Optional[ServerConfig] = None
) -> FastAPI:
    """装配应用。Settings.load() 必须先于 ServerConfig.from_env()：
    前者把 .env 写入 os.environ，后者才能读到 SERVER_* / API_TOKENS 等值。"""
    settings = settings or Settings.load()
    config = config or ServerConfig.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.pool = AgentPool(settings)
        app.state.server_config = config
        logger.info(
            "后端就绪：%s:%s，鉴权用户 %d 个", config.host, config.port, len(config.tokens)
        )
        yield
        # 先等后台记忆沉淀写完，再释放连接池与 SDK 资源（否则最后几条记忆可能丢失）
        await app.state.pool.close()

    app = FastAPI(title="医疗知识图谱 Agent 后端", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins) or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    cfg = ServerConfig.from_env()
    uvicorn.run(app, host=cfg.host, port=cfg.port)
