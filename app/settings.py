"""统一配置层。

把散落在环境变量里的配置，集中转成 openjiuwen 需要的各类 Config 对象。
本模块是唯一读取环境变量的地方，其他模块只消费这里产出的对象。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from openjiuwen.core.common.logging.log_config import (
    configure_log_config,
    get_log_config_snapshot,
)
from openjiuwen.core.single_agent import ReActAgentConfig

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

BASE_SYSTEM_PROMPT = (
    "你是中文医疗知识助手。知识图谱是你的检索工具、不是回答本身："
    "图谱负责提供事实依据，你负责用自己的医学知识解释、整合这些事实，"
    "输出面向人的自然语言回答。\n"
    "1. 回答涉及具体疾病、症状、药物、基因、蛋白时，先按技能手册调用图谱工具取事实依据。\n"
    "2. 图谱返回的实体与关系是事实依据；你可以也应当用自己的医学知识解释这些事实"
    "（作用机制、临床意义、背景知识），但不得编造图谱中不存在的具体实体或关系；"
    "你的知识与图谱冲突时以图谱为准。\n"
    "3. 不要在回答里暴露内部细节：不要输出 entity_key、relation_code、工具名、检索步骤，"
    "也不要写“检索结果”“实体选择”“关系类型说明”这类查询日志；"
    "描述关系时用中文语义名（如“分类层级关系”“适应证”“禁忌”），"
    "不要出现 parent_child、associated_with 这类内部代号；"
    "确需点明出处时用自然语言（如“图谱中记录 ACE 与高血压相关”）。\n"
    "4. 回答形态：先用一两句给结论性概述，再展开要点与依据；中文，"
    "像医生向患者解释那样自然连贯，完整成句，不要罗列工具输出。\n"
    "5. 英文术语（基因/蛋白/药物名）按需调用 translate_terms 翻译；"
    "基因与蛋白符号原样保留。\n"
    "6. 医疗知识图谱的详细使用手册见技能 medical-knowledge-graph"
    "（含工具调用顺序、关系类型词表、数据约束）；涉及图谱检索时先读取该技能再动手。"
)


def _load_env_file(path: Path) -> None:
    """极简 .env 加载器，避免引入额外依赖。已存在的环境变量优先。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def quiet_logs(level: str = "ERROR") -> None:
    """压低 openjiuwen 的日志级别，并改为只写文件，避免刷屏。"""
    snapshot = get_log_config_snapshot()
    snapshot["level"] = level
    snapshot["output"] = ["file"]
    snapshot["interface_output"] = ["file"]
    snapshot["performance_output"] = ["file"]
    configure_log_config(snapshot)


def _env_flag(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # ---- 对话模型（OpenAI 兼容）----
    # BYOK 模式下这只是「默认/兜底」配置：用户的 key 经 ChatRequest.llm 按请求传入，
    # 经 dataclasses.replace 覆盖这三个字段，服务端不持久化用户 key。
    api_key: str = field(repr=False)  # 不进 repr，避免日志泄 key
    base_url: str
    model: str
    # ---- 向量化（独立于对话侧）----
    embedding_model: str
    embedding_base_url: str
    embedding_api_key: str = field(repr=False)
    # ---- Neo4j 图谱 ----
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str = field(repr=False)
    neo4j_database: str
    # graph_backend: auto（能连上 Neo4j 就用，否则降级样例数据）/ neo4j / sample
    graph_backend: str
    sample_graph: str
    # ---- 本地存储 ----
    sqlite_path: str
    chroma_dir: str
    skills_dir: str
    # ---- 长期记忆 ----
    memory_scope_id: str
    memory_enable_profile: bool
    memory_enable_semantic: bool
    memory_enable_episodic: bool
    memory_enable_summary: bool
    # ---- 行为参数 ----
    max_iterations: int
    context_window_rounds: int
    llm_rpm_limit: int
    system_prompt: str = BASE_SYSTEM_PROMPT

    @classmethod
    def load(cls) -> "Settings":
        _load_env_file(ENV_FILE)

        def env(name: str, default: str = "") -> str:
            return os.environ.get(name, default).strip()

        api_key = env("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "缺少 OPENAI_API_KEY。请复制 .env.example 为 .env 并填入真实值。"
            )

        base_url = env("OPENAI_BASE_URL", "https://api.deepseek.com/v1")

        return cls(
            api_key=api_key,
            base_url=base_url,
            model=env("OPENAI_MODEL", "deepseek-chat"),
            # 对话与向量化可以来自不同厂商（DeepSeek 对话 + 百炼向量化）。
            # DeepSeek 同样不提供 embedding 接口，必须独立配置 EMBEDDING_*。
            # 注意：EMBEDDING_BASE_URL 必须是「完整 endpoint」——openjiuwen 会直接
            # POST 这个地址，不会再拼 /embeddings；未配置时按对话侧 base_url 推导。
            embedding_model=env("EMBEDDING_MODEL", "text-embedding-v3"),
            embedding_base_url=env("EMBEDDING_BASE_URL")
            or f"{base_url.rstrip('/')}/embeddings",
            embedding_api_key=env("EMBEDDING_API_KEY") or api_key,
            neo4j_uri=env("NEO4J_URI", "bolt://localhost:7687"),
            neo4j_user=env("NEO4J_USER", "neo4j"),
            neo4j_password=env("NEO4J_PASSWORD"),
            neo4j_database=env("NEO4J_DATABASE", "neo4j"),
            graph_backend=env("GRAPH_BACKEND", "auto").lower(),
            sample_graph=env("SAMPLE_GRAPH") or str(ROOT / "data" / "sample_graph.csv"),
            sqlite_path=env("SQLITE_PATH") or str(ROOT / "data" / "app.db"),
            chroma_dir=env("CHROMA_DIR") or str(ROOT / "data" / "chroma"),
            skills_dir=env("SKILLS_DIR") or str(ROOT / "skills"),
            memory_scope_id=env("MEMORY_SCOPE_ID", "medical-kg-course"),
            memory_enable_profile=_env_flag("MEMORY_ENABLE_PROFILE"),
            memory_enable_semantic=_env_flag("MEMORY_ENABLE_SEMANTIC"),
            memory_enable_episodic=_env_flag("MEMORY_ENABLE_EPISODIC"),
            memory_enable_summary=_env_flag("MEMORY_ENABLE_SUMMARY"),
            max_iterations=int(env("MAX_ITERATIONS", "8")),
            context_window_rounds=int(env("CONTEXT_WINDOW_ROUNDS", "10")),
            # 全项目共享的 LLM 限流上限（每分钟请求数）。正常使用不加延迟，
            # 只有最近 60 秒内请求数达到该值才等待；设为 0 即完全关闭节流。
            llm_rpm_limit=int(env("LLM_RPM_LIMIT", "100")),
        )


def build_react_config(settings: Settings, sys_operation_id: str = "") -> ReActAgentConfig:
    """组装 ReActAgentConfig。

    - 模型：provider 固定 openai，走 OpenAI 兼容协议（换厂商只改 base_url）
    - 上下文窗口：context_engine_config 控制保留轮数
    - 系统操作：sys_operation_id 用于本地文件读写能力
    """
    prompt_template: List[Dict[str, Any]] = [
        {"role": "system", "content": settings.system_prompt}
    ]
    cfg = (
        ReActAgentConfig()
        # configure_model_provider 只存字符串字段，不会创建 model_client_config；
        # _get_llm 运行期要求 model_client_config 非空，必须用 configure_model_client。
        .configure_model_client(
            "openai", settings.api_key, settings.base_url, settings.model
        )
        .configure_prompt_template(prompt_template)
        .configure_max_iterations(settings.max_iterations)
        .configure_context_engine(
            default_window_round_num=settings.context_window_rounds
        )
    )
    if sys_operation_id:
        cfg.sys_operation_id = sys_operation_id
    return cfg
