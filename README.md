# openjiuwen-agent · 中文医疗知识图谱 Agent（Agent 层）

基于官方 [openjiuwen](https://pypi.org/project/openjiuwen/) SDK 的**中文医疗知识图谱问答 Agent**。
三层全交付：Agent 层（`app/`）+ FastAPI 后端（`server/`，见"启动后端"节）+
React 前端（`frontend/`，见"启动前端"节）。

> 完整构建决策、差异说明、风险与实测记录见 [项目构建细节.md](项目构建细节.md)（唯一的构建基线）。

## 能力

| 能力 | 实现 |
| --- | --- |
| 对话推理 | openjiuwen `ReActAgent`（provider 固定 `openai`，走 OpenAI 兼容协议） |
| 图谱检索 | 5 个**白名单工具**（搜索实体 / 实体详情 / 一跳关系 / 最短路径 / 子图），Cypher 由服务层参数化生成，**模型永远不能自由拼查询**；已导入 PrimeKG-Plus 全量（**129,317 节点 / 7,683,206 关系**） |
| 短期上下文 | openjiuwen `ContextEngine`（按 `session_id` 维护最近 N 轮）+ 本地消息归档 |
| 长期记忆 | openjiuwen 内置 `openjiuwen.core.memory` 引擎 + SQLite + Chroma 向量库，按 `user_id` + `scope_id` 隔离，跨会话召回 |
| 按需翻译 | 英文术语批量翻译 + SQLite 缓存（缓存键含 prompt 版本），**命中缓存不产生 LLM 请求** |
| 流式响应 | `handle_chat_stream` 边推理边推事件（`tool_call` / `tool_result` / `delta` / `done`），后端可直接包成 SSE |
| 会话安全 | session 归属校验（跨用户串会话抛 `SessionOwnershipError`）、`request_id` 幂等 |

## 架构

```
        后端 FastAPI（server/）               本仓库（Agent 层）                    openjiuwen SDK
┌───────────────────────────────┐   ┌──────────────────────────────┐   ┌────────────────────────┐
│ 鉴权 → 取 user_id             │   │ schemas.py   接口契约        │──▶│ ReActAgent + ReActAgentConfig │
│ POST /api/chat（/stream SSE） │──▶│ AgentService.handle_chat[_stream]() │──▶│ Runner.run_agent[_streaming] / 上下文引擎 │
│ 透传 ChatResponse / SSE 事件  │◀──│ services/    业务编排        │──▶│ @tool 工具能力管理器          │
│                               │   │ repositories/ 数据访问       │──▶│ 长期记忆引擎 + 向量库         │
└───────────────────────────────┘   │ factory.py    装配工厂        │   └────────────────────────┘
                                    └──────────────────────────────┘
```

分层铁律：`settings` 是唯一读环境变量的地方；`repositories` 只做数据读写（不调 LLM、不做业务判断）；
`services` 只做编排（不拼 SQL/Cypher）；`tools` 只做参数适配；只有 `factory.py` 知道怎么拼装。

## 目录

```
app/
├── settings.py                 统一配置层（唯一读环境变量的地方）
├── schemas.py                  对后端/前端的 Pydantic 契约
├── throttle.py                 全项目共享的 LLM 节流闸门（滑动窗口 RPM 限流）
├── factory.py                  装配工厂：build_agent() / close()
├── services/
│   ├── agent_service.py        单轮对话总编排（后端唯一调用点）
│   ├── context_service.py      短期上下文 + 会话归属守卫 + 归档
│   ├── memory_service.py       长期记忆检索/沉淀/降级（Engine 端口）
│   ├── graph_query_service.py  5 个图谱能力的后端实现
│   └── translation_service.py  按需翻译 + 缓存
├── repositories/
│   ├── conversation_store.py   会话归档（抽象 + SQLite，PG 可替换）
│   ├── graph_repository.py     图数据访问（抽象 + Neo4j + CSV 样例降级）
│   └── translation_cache.py    翻译缓存表
├── tools/
│   ├── graph_tools.py          5 个图谱工具（模型可见）
│   └── translation_tools.py    1 个翻译工具（模型可见）
└── memory/setup.py             内置长期记忆引擎装配

server/                          FastAPI 后端路由层（HTTP ⇄ AgentPool 薄转发）
├── main.py                     全部路由 + SSE 映射 + 应用装配
├── auth.py                     JWT 签发/校验（HS256，sub=user_id）
├── user_store.py               用户表（data/server.db）+ PBKDF2 口令哈希
└── config.py                   服务监听 / CORS / JWT（SERVER_*、JWT_* 环境变量）

frontend/                        React 前端（Vite 构建）
├── src/api.js                  JWT 请求封装 + SSE POST 流式解析（BYOK 只存 localStorage）
├── src/App.jsx                 登录态管理（令牌失效自动回登录页）
├── src/Login.jsx               登录/注册
├── src/ChatPanel.jsx           两栏主界面：会话列表 + 消息流（流式/工具状态/证据标签）
└── src/SettingsModal.jsx       BYOK 模型设置（浏览器侧保存）

examples/                       01_chat / 02_memory / 03_translation
scripts/import_sample.py        导入小样例图谱到 Neo4j
scripts/import_primekg.py       导入 PrimeKG-Plus 全量到 Neo4j
scripts/stream_e2e.py           流式响应端到端回归验证（事件序列 + delta 拼接一致性）
skills/                         技能手册（<name>/SKILL.md，由模型按需 read_file 加载）
data/                           SQLite / Chroma / 样例 CSV（运行期产物）
data/primekg/                   PrimeKG-Plus 数据包（dataset/ + Zenodo 压缩包）
```

> `skills/medical-knowledge-graph/SKILL.md` 是图谱操作手册（工具调用顺序、关系类型词表、
> 数据约束）。基础提示词只留角色定位与通用规则，手册正文由模型按需加载；`read_file` 工具
> 由 `factory.build_agent` 显式注入，**SDK 不自动提供，缺失会导致技能静默失效**。

## 快速跑通

从零到第一轮对话，最短路径（**不用 Docker、不导全量图谱也能跑通**）：

| # | 步骤 | 命令 / 操作 | 通过标志 |
| --- | --- | --- | --- |
| 1 | 建环境装依赖 | `python -m venv .venv` → `.\.venv\Scripts\python.exe -m pip install -e .` | `python -c "import app"` 不报错 |
| 2 | 配 `.env` | `copy .env.example .env`，**必填** `OPENAI_API_KEY`；embedding 二选一：本地 TEI（见"起本地 embedding"节，`EMBEDDING_API_KEY=local` 任意非空）或云端百炼（填真实 key） | — |
| 3 | 图谱（二选一） | ① 无 Docker：什么都不做，`GRAPH_BACKEND=auto` 自动降级读 `data/sample_graph.csv`<br>② 有 Docker：起容器（见"起图谱"节）→ `.\.venv\Scripts\python.exe scripts\import_sample.py` 导样例（22 实体 / 23 关系） | ① 零配置；② 脚本打印导入完成 |
| 4 | 跑对话 | `.\.venv\Scripts\python.exe examples\01_chat.py` | 回答引用图谱证据，`evidence_entities` 非空；降级时 `meta.graph_backend="sample"` |
| 5 | 跑记忆 | `.\.venv\Scripts\python.exe examples\02_memory.py` | 换全新 `session_id` 仍召回上一会话信息（`memory_used > 0`） |
| 6 | 跑翻译 | `.\.venv\Scripts\python.exe examples\03_translation.py` | 第二/三次调用 `llm_calls` 不再增长（缓存命中，零 LLM 请求） |

注意：

- 全部命令用项目内 `.venv` 的 Python 执行；单轮对话约 90~110 秒属正常（大头是模型往返）
- **`request_id` 有幂等缓存**：重复跑同一示例会命中旧答案、不产生新 LLM 调用；想验证改动效果就换新的 `request_id` / `session_id`
- 第 4 步即已覆盖全链路（归属校验 → 上下文 → 记忆检索 → ReAct + 图谱工具 → 写回 → 后台沉淀）；5、6 是增强能力的单独验证
- 流式链路回归：`.\.venv\Scripts\python.exe scripts\stream_e2e.py`（事件序列 + delta 拼接一致性断言）

## 启动后端

前置：装好依赖（`pip install -e .`，已含 fastapi/uvicorn）、配好 `.env`（同"快速跑通"第 2 步）。

```powershell
.\.venv\Scripts\python.exe -m server.main          # 默认 127.0.0.1:8000
# 或
.\.venv\Scripts\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

- **鉴权**：用户表 + JWT。`POST /api/auth/register` 注册（可用 `ALLOW_REGISTRATION=false` 关闭）、
  `POST /api/auth/login` 登录，返回 `access_token`；业务接口带 `Authorization: Bearer <JWT>`。
  口令以 PBKDF2-HMAC-SHA256（60 万次迭代）哈希后存 `data/server.db`（`USER_DB_PATH` 可改）；
  签名密钥 `JWT_SECRET` **生产必须显式配置**（留空则每次启动随机生成，重启后令牌全部失效），
  有效期 `JWT_EXPIRE_MINUTES`（默认 720 分钟）。
- **接口**：`POST /api/auth/register`、`POST /api/auth/login`、`GET /api/auth/me`、
  `POST /api/chat`、`POST /api/chat/stream`（SSE，事件 `tool_call` / `tool_result` /
  `delta` / `done` / `error`，静默期每 20s 发 `: ping` 注释行）、`GET /api/entities/{key}`、
  `GET /api/entities/{key}/subgraph?depth=1&limit=50`、`DELETE /api/users/me/memory`、
  `GET /api/sessions`、`GET /api/sessions/{id}/messages`、`DELETE /api/sessions/{id}`、
  `GET /api/health`。完整契约（错误码 / SSE 字段 / 联调检查单）见 [接口文档.md](接口文档.md)。
- 服务端环境变量（`SERVER_HOST` / `SERVER_PORT` / `JWT_SECRET` / `CORS_ORIGINS` 等）也写在 `.env` 里，
  模板见 [.env.example](.env.example)。

## 启动前端

前置：Node.js ≥ 18；后端已启动（默认 127.0.0.1:8000）。

```powershell
cd frontend
npm install
npm run dev        # http://localhost:5173（/api 已代理到本地后端）
npm run build      # 生产构建到 frontend/dist（由同源反代或静态服务承载）
```

- 页面功能：注册/登录（JWT 存 localStorage）、会话列表（新建/切换/删除）、SSE 流式问答
  （工具调用状态行 + 逐字生成）、证据实体标签展示、BYOK 模型设置（仅存浏览器）、
  清空长期记忆、退出登录。
- 已实测：注册登录 → 流式问答（约 30s）→ 证据标签展示 → 退出重登会话仍在，控制台无报错。

## 安装

```powershell
# Python >= 3.11,<3.14
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

## 配置

```powershell
copy .env.example .env
# 编辑 .env：OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL / NEO4J_PASSWORD / EMBEDDING_API_KEY
```

- **对话模型**：provider 固定 `openai`，默认 DeepSeek `deepseek-chat`；换厂商只改 `.env` 的
  `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` 三项（任何 OpenAI 兼容接口均可，
  如百炼 `qwen-plus`、智谱 `glm-4-plus`）
- **向量化**：必须独立配置 `EMBEDDING_*`（DeepSeek **不提供 embedding 接口**）。两种方式二选一：
  - **本地（推荐）**：Docker 起 TEI 容器（`oj-embed`，`BAAI/bge-m3`，见下方"起本地 embedding"节），
    离线可用、零配额，BYOK 用户不再共享服务端 embedding
  - **云端**：百炼 DashScope `text-embedding-v3`
  注意 `EMBEDDING_BASE_URL` 要填**完整 endpoint**（openjiuwen 会直接 POST 该地址，不再补
  `/embeddings`）；**切换提供方后必须清空 `data/chroma` 与 SQLite 旧记忆**（向量空间不兼容）
- **节流**：`LLM_RPM_LIMIT` 滑动窗口限流（默认 100/分钟），Agent 推理 / 记忆抽取 / 翻译
  共用同一闸门；额度内立即放行、不加任何延迟，仅窗口内请求数达上限时才等待；设为 0 即完全关闭节流
- 所有环境变量含义见 `.env.example` 注释

## 起本地 embedding（可选，替代云端百炼）

用 Docker 单容器跑 text-embeddings-inference（TEI）CPU 版，模型 `BAAI/bge-m3`
（中文友好，1024 维，约 2.3GB，CPU 可跑）：

```powershell
# 1) 先下载模型到本地（TEI 自带下载器与镜像站不兼容，必须用 huggingface_hub 预下载）
$env:HF_ENDPOINT='https://hf-mirror.com'
.\.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-m3', local_dir=r'data\models\bge-m3')"

# 2) 挂载模型目录起容器（max-batch-tokens 4096 是为压低 warmup 内存峰值，须配合 auto-truncate）
docker run -d --name oj-embed -p 8080:80 -v <项目路径>/data/models:/data `
  ghcr.io/huggingface/text-embeddings-inference:cpu-1.8.3 --model-id /data/bge-m3 `
  --max-batch-tokens 4096 --max-concurrent-requests 32 --auto-truncate
```

`.env` 对应三项：`EMBEDDING_MODEL=bge-m3`、`EMBEDDING_BASE_URL=http://localhost:8080/v1/embeddings`、
`EMBEDDING_API_KEY=local`（任意非空值）。记忆检索/沉淀的 embedding 调用随即全部走本地，
离线可用、零配额。SDK 的 scope 检索路径硬编码 OpenAI 兼容 `APIEmbedding`，因此本地必须走
HTTP 端点（进程内嵌入插不进去），TEI 是最省心的选择。

## 起图谱（可选，不起也能跑）

有 Docker 时用单容器起 Neo4j（容器名 `oj-neo4j`，镜像 `neo4j:5.26-community`，端口 7474/7687）：

```powershell
docker run -d --name oj-neo4j -p 7474:7474 -p 7687:7687 `
  -v oj-neo4j-data:/data -v oj-neo4j-logs:/logs `
  -e NEO4J_AUTH=neo4j/<你的密码> `
  -e NEO4J_server_memory_heap_max__size=2G `
  -e NEO4J_server_memory_pagecache_size=2G `
  neo4j:5.26-community
```

内存参数 **heap 2G + pagecache 2G**（导入全量后重建调大，原为 512M/512M）。
**注意单事务内存上限 1.4 GiB**：一次性 `MATCH (n:Entity) DETACH DELETE n` 清全图会报
`MemoryPoolOutOfMemoryError` 并整体回滚；`import_primekg.py::wipe_entities()` 采用**两阶段分批**
（先删关系 20000/批，再删已无关系的节点 2000/批）。若要彻底清空，重建容器挂新卷最快。

### 小样例（22 实体 / 23 关系）

```powershell
.\.venv\Scripts\python.exe scripts\import_sample.py
```

### 全量 PrimeKG-Plus（129,317 节点 / 7,683,206 关系）

数据源：PrimeKG-Plus（GitHub `DSDD-UCPH/PrimeKG-Plus`，Zenodo DOI `10.5281/zenodo.20796545`）。
下载包 `data/primekg/zenodo_bundle.tar.gz`（568 MB，保留未删），解包后为 `data/primekg/dataset/`
（`primekg_plus.csv` 901 MB、`nodes.csv` 7.5 MB）。

```powershell
.\.venv\Scripts\python.exe scripts\import_primekg.py                  # 默认 plus，导入前先清空
.\.venv\Scripts\python.exe scripts\import_primekg.py --dry-run        # 只解析统计，不写库
```

- 脚本参数：`--bundle`（数据目录，默认 `data/primekg/dataset`）、`--variant plus|rd`、
  `--batch-size`（默认 10000）、`--keep-existing`、`--dry-run`
- 流式读取 + 分批 `UNWIND` 写入，实测约 **410 秒**、约 **21,000 关系/秒**
- 写入模型与 `scripts/import_sample.py` 一致（`:Entity` / `:RELATED_TO`），
  `GraphQueryService` 与 5 个图谱工具**无需改任何代码**即可用于全量数据
- `primekg_plus_rd.csv`（罕见病文献增补版）在 tar 包内是**符号链接**，指向
  `dataset/literature_curation/20260529-primekg_plus_rd.csv`（945 MB），当前**未抽取、未导入**，
  需要时再从 bundle 里取

没起 Neo4j 时 `GRAPH_BACKEND=auto` 会自动降级到 `data/sample_graph.csv`，
Agent 链路依然端到端可用（这是降级路径，不是替代方案）。

## 运行示例

```powershell
.\.venv\Scripts\python.exe examples\01_chat.py         # 图谱问答 + 证据实体
.\.venv\Scripts\python.exe examples\02_memory.py       # 跨会话长期记忆召回
.\.venv\Scripts\python.exe examples\03_translation.py  # 翻译缓存命中（零 LLM 请求）
```

限流不再给正常使用加延迟，单轮耗时主要来自模型自身的生成（技能手册含"效率规则"引导模型最少化工具调用，
实测优化后端到端单轮约 90~110 秒，其中 ~98% 是模型往返）；如遇厂商限流，调小 `LLM_RPM_LIMIT` 即可，
无需在脚本之间留间隔。

> 运行环境是项目内的 `.venv`（Python 3.12.7），示例与脚本请用 `.\.venv\Scripts\python.exe` 执行。
> 注意 `request_id` 有幂等缓存：用同一 `request_id` 重复跑会直接命中旧答案、不产生新的 LLM 调用；
> 若要验证新的系统提示词，需换新的 `request_id` / `session_id`。

## 后端怎么接

生产路径（BYOK：用户自带 key，服务端不持久化）：

```python
from app import AgentPool, ChatRequest, LLMConfig, Settings

pool = AgentPool(Settings.load())                # 进程启动时建池（共享图谱/会话库/翻译缓存）

resp = await pool.handle_chat(
    ChatRequest(
        user_id=鉴权取到的用户,                   # 必须来自后端鉴权，不得由前端传入
        session_id=会话号,
        query=用户输入,
        request_id=请求号,
        llm=LLMConfig(                            # 前端保存在浏览器侧，每次请求随 body 传入
            api_key=用户自己的key,                # 只在内存中使用：不落库、不进日志、不进 meta
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
        ),
    )
)
# resp.answer / resp.evidence_entities / resp.memory_used / resp.meta

await pool.close()                               # 进程退出前必须调用
```

流式路径（后端包成 SSE，`text/event-stream`）：

```python
async for event in pool.handle_chat_stream(req):   # 同样的 ChatRequest
    # event: {"event": "tool_call"|"tool_result", "name": ...}
    #        {"event": "delta", "text": ...}        # 回答增量，按序拼接即完整回答
    #        {"event": "done", "response": {...}}    # 完整 ChatResponse，连接收尾
    ...                                             # 每个 dict 序列化为 event:/data: 两行即可
```

`llm` 缺省时使用 `.env` 的默认配置（示例脚本与自测路径）；相同 (base_url, model, api_key)
的 Agent 会被池缓存复用，池满按 LRU 逐出。

单 Agent 路径（`build_agent` / `close`）仍保留，供 examples 使用：

```python
from app import ChatRequest, Settings, build_agent, close

settings = Settings.load()
built = await build_agent(settings)              # 进程启动时装配一次，复用

resp = await built.service.handle_chat(
    ChatRequest(user_id=鉴权取到的用户, session_id=会话号, query=用户输入, request_id=请求号)
)
# resp.answer / resp.evidence_entities / resp.memory_used / resp.meta

await close(built)                               # 进程退出前必须调用，释放连接与 SDK 资源
```

约定：

1. 只 import `app.services` 与 `app.schemas`，不要碰 `repositories/`、`tools/`、`memory/`
2. `user_id` 必须来自鉴权结果，**不得**由前端传入
3. 用户 LLM key 只经 `ChatRequest.llm` 按请求传入（前端存浏览器侧）；服务端任何模块
   不得把它写入 SQLite / Chroma / 日志 / meta
4. `evidence_entities` 是前端渲染证据标签的唯一依据，不要自行解析回答文本
5. 换 PostgreSQL：替换 `ConversationStore` 实现即可（接口不变）；记忆后端若要 PG 需自行写适配器
   （SDK 无 PG 记忆适配器，见构建细节 §8）

## 已知约束

- Python `>=3.11,<3.14`
- `filelock` 必须锁在 `3.x`（`>=3.25,<4`），4.x 会破坏 SDK 本地 fs 读取
- LLM 调用按 `LLM_RPM_LIMIT`（滑动窗口，默认 100/分钟）全项目共享限流：额度内不加延迟，
  仅窗口内请求数达上限时才等待最早的请求滑出窗口；设为 0 即完全关闭节流
- 运行日志默认写 `./logs/`（控制台已静默到 ERROR），记忆/图谱检索失败会降级且不阻断对话
- **疾病与疾病之间只有 `parent_child`（本体层级）一种关系**（全图 67,720 条全是它），
  图谱里没有任何"共病 / 并发症 / 风险增加"类关系。因此"高血压有哪些相关疾病 / 并发症"这类问题
  **无法从本图谱直接回答**，只能说明只有分类层级信息；回答时会结合 LLM 自身医学知识补充，
  并讲清哪些是图谱证据、哪些是模型知识
- `relation_code` 是**归一化后的关系名**（小写 + 空格和连字符转下划线：`associated with`→
  `associated_with`、`parent-child`→`parent_child`、`off-label use`→`off_label_use`）；
  全量图实际只有 **18 种**，关系类型以工具返回的 `relation_counts` 为准
- 图谱**每条关系在正反两个方向各存一行**，`src_key` / `dst_key` 不表示主体与客体，
  不能按方向推断因果
- `MONDO_grouped:<id>_<id>_...` 是**多个 MONDO 术语合并**的节点，其层级邻居可能语义无关，
  应优先选用具体子型（如 `MONDO:1134` essential hypertension）；
  合并映射表见 `data/primekg/auxillary/kg_grouped_diseases_bert_map.csv`
