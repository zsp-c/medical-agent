"""pytest 公共装配：把仓库根目录放进 sys.path。

`app` 与 `server` 都是仓库根下的包，直接 `pytest` 时根目录不一定在导入路径里，
因此在这里统一注入一次，避免每个测试文件各写一份 sys.path 处理。
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
