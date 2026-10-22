"""FastAPI 应用：全部 HTTP 接口的装配与路由（接口契约见 接口文档.md）。

启动（项目 venv，需先 `pip install -e .` 装上 fastapi/uvicorn）：

    .\\.venv\\Scripts\\python.exe -m server.main
    # 或
    .\\.venv\\Scripts\\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000

接口一览：
- POST   /api/auth/register                   注册（可用 ALLOW_REGISTRATION=false 关闭）
- POST   /api/auth/login                      登录，返回 JWT
- GET    /api/auth/me                         当前用户信息
- POST   /api/chat                            非流式问答
- POST   /api/chat/stream                     SSE 流式（tool_call/tool_result/delta/done）
- POST   /api/files                           上传并解析文件（multipart，返回解析状态）
- GET    /api/files                           当前用户文件列表
- DELETE /api/files/{file_id}                 删除文件（磁盘与数据行一起清）
- GET    /api/entities/{entity_key}           实体详情
- GET    /api/entities/{entity_key}/subgraph  局部子图
- DELETE /api/users/me/memory                 清空当前用户长期记忆
- GET    /api/sessions                        会话列表
- GET    /api/sessions/{session_id}/messages  会话历史消息
- DELETE /api/sessions/{session_id}           删除会话
- GET    /api/health                          存活探针（免鉴权）

鉴权：Authorization: Bearer <JWT>（注册/登录签发，sub=user_id，过期时间见
JWT_EXPIRE_MINUTES；签名密钥 JWT_SECRET 必须在 .env 显式配置，否则重启即失效）。
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any, AsyncIterator, Dict, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.factory import AgentPool
from app.schemas import ChatRequest, ChatResponse, GraphResult, LLMConfig
from app.services import SessionOwnershipError
from app.services.file_service import UploadRejectedError
from app.settings import Settings
from server.auth import create_access_token, current_user
from server.config import ServerConfig
from server.user_store import UserStore, UsernameTakenError

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
    attachments: List[str] = Field(default_factory=list)
    options: Dict[str, Any] = Field(default_factory=dict)

    def to_chat_request(self, user_id: str) -> ChatRequest:
        # request_id 缺省时服务端补一个，便于追溯；网络重试要触发幂等仍需前端复用同一值
        return ChatRequest(
            user_id=user_id,
            session_id=self.session_id,
            query=self.query,
            request_id=self.request_id or str(uuid.uuid4()),
            llm=self.llm,
            attachments=self.attachments,
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


# --------------------------------------------------------------------------- #
# 账号体系：注册 / 登录 / 当前用户（JWT 签发）
# --------------------------------------------------------------------------- #
class AuthBody(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_-]{3,32}$")
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # 秒
    user_id: str
    username: str


def _user_store(request: Request) -> UserStore:
    return request.app.state.user_store


def _issue_token(request: Request, user: Dict[str, str]) -> TokenResponse:
    cfg: ServerConfig = request.app.state.server_config
    return TokenResponse(
        access_token=create_access_token(cfg, user["user_id"]),
        expires_in=cfg.jwt_expire_minutes * 60,
        user_id=user["user_id"],
        username=user["username"],
    )


@router.post("/auth/register", response_model=TokenResponse, status_code=201)
def register(body: AuthBody, request: Request) -> TokenResponse:
    """注册并直接返回 JWT（免二次登录）。可用 ALLOW_REGISTRATION=false 关闭。"""
    cfg: ServerConfig = request.app.state.server_config
    if not cfg.allow_registration:
        raise HTTPException(status_code=403, detail="注册已关闭，请联系管理员开通账号")
    try:
        user = _user_store(request).create_user(body.username, body.password)
    except UsernameTakenError:
        raise HTTPException(status_code=409, detail="用户名已被注册")
    return _issue_token(request, user)


@router.post("/auth/login", response_model=TokenResponse)
def login(body: AuthBody, request: Request) -> TokenResponse:
    """口令校验成功返回 JWT；失败一律 401（不区分用户不存在/密码错误，防枚举）。"""
    user = _user_store(request).authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return _issue_token(request, user)


@router.get("/auth/me")
def auth_me(user: CurrentUser, request: Request) -> Dict[str, str]:
    """当前登录用户信息（校验令牌有效性的轻量探针）。"""
    info = _user_store(request).get(user)
    if info is None:  # 用户被删但令牌未过期
        raise HTTPException(status_code=401, detail="账号不存在或已注销")
    return info


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


# --------------------------------------------------------------------------- #
# 上传文件：multipart 上传 → 落盘 + 解析成纯文本入库；发消息时按 file_id 注入对话
# --------------------------------------------------------------------------- #
@router.post("/files", status_code=201)
async def upload_files(
    user: CurrentUser,
    request: Request,
    files: List[UploadFile] = File(...),
    llm: Optional[str] = Form(default=None),
) -> Dict[str, Any]:
    """上传并解析文件（每个文件独立返回解析状态，失败的文件不影响其他文件）。

    - 单次最多 upload_max_files 个、每个 ≤ upload_max_mb（超出 400，文案可读）
    - `llm` 为可选表单字段（JSON 字符串）：图片 caption 用的视觉模型配置，
      仅本次请求内存使用，不持久化；缺省用服务端默认模型
    """
    settings: Settings = request.app.state.settings
    if len(files) > settings.upload_max_files:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多上传 {settings.upload_max_files} 个文件",
        )
    llm_cfg: Optional[LLMConfig] = None
    if llm:
        try:
            llm_cfg = LLMConfig.model_validate_json(llm)
        except ValueError:
            raise HTTPException(status_code=400, detail="llm 字段不是合法的模型配置 JSON")
    service = _pool(request).file_service()
    results = []
    for upload in files:
        data = await upload.read()
        try:
            results.append(
                await service.ingest(user, upload.filename or "file", data, llm_cfg)
            )
        except UploadRejectedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("文件上传失败", exc_info=True)
            raise HTTPException(status_code=500, detail="文件保存失败，请稍后重试") from exc
    return {"files": results}


@router.get("/files")
def list_files(user: CurrentUser, request: Request) -> Dict[str, Any]:
    """当前用户的文件列表（新→旧，不含正文）。"""
    return {"files": _pool(request).file_service().list_files(user)}


@router.delete("/files/{file_id}")
def delete_file(file_id: str, user: CurrentUser, request: Request) -> Dict[str, bool]:
    """删除文件：磁盘目录与数据行一起清。他人文件 403。"""
    result = _pool(request).file_service().delete_file(user, file_id)
    if result is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if result is False:
        raise HTTPException(status_code=403, detail="无权访问该文件")
    return {"ok": True}


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
    """局部子图。上限 depth≤2、limit≤50。"""
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
    前者把 .env 写入 os.environ，后者才能读到 SERVER_* / JWT_* 等值。"""
    settings = settings or Settings.load()
    config = config or ServerConfig.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.pool = AgentPool(settings)
        app.state.settings = settings
        app.state.server_config = config
        app.state.user_store = UserStore(config.user_db_path)
        logger.info(
            "后端就绪：%s:%s（用户库 %s，注册%s，令牌有效期 %d 分钟%s）",
            config.host,
            config.port,
            config.user_db_path,
            "开放" if config.allow_registration else "关闭",
            config.jwt_expire_minutes,
            "，JWT_SECRET 为临时随机值" if config.jwt_secret_ephemeral else "",
        )
        yield
        # 先等后台记忆沉淀写完，再释放连接池与 SDK 资源（否则最后几条记忆可能丢失）
        await app.state.pool.close()
        app.state.user_store.close()

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
