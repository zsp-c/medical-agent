"""内置长期记忆引擎装配（对应构建细节 §7）。"""

from app.memory.setup import OpenjiuwenMemoryEngine, build_memory_engine

__all__ = ["OpenjiuwenMemoryEngine", "build_memory_engine"]
