"""模型可调用的工具集合（白名单）。

图谱工具 5 个 + 题库工具 2 个。新增能力请放在本目录，并在 `app/factory.py` 里注册。
"""

from app.tools.graph_tools import make_graph_tools
from app.tools.question_tools import make_question_tools

__all__ = ["make_graph_tools", "make_question_tools"]
