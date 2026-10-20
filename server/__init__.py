"""FastAPI 后端路由层：HTTP 接口 ⇄ Agent 层（app.factory.AgentPool）的薄转发。

对接铁律（见 接口文档.md §一）：
- 只 import app.factory / app.schemas / app.services，不触碰 repositories/tools
- user_id 一律来自服务端鉴权（Bearer 令牌表），请求体不收
- 用户 LLM key 只在请求内存中流转，不写日志与持久层
"""
