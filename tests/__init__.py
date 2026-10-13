"""测试包。

测试模块之间用顶层名互相引用（`from support import …`），因此把本目录加进
`sys.path`：无论用 `python -m unittest discover -s tests -t tests` 还是从仓库根跑
`python -m unittest discover`，两种发现方式都能解析到同一个 `support` 模块。

解释器分两类。不 import maibot_sdk、任意 Python 3.11+ 都能跑的九个模块：
`test_access_gate`、`test_config_rules`、`test_config_toml`、`test_history_reader`、
`test_manifest`、`test_stream_resolver`、`test_summary_generator`、`test_summary_store`、
`test_target_params`。需要 maibot_sdk、必须用装了 SDK 的解释器的六个模块：
`test_config_model`、`test_plugin_components`、`test_plugin_config_update`、
`test_read_chat_summary`、`test_tool_structure`、`test_tools_execute`
（本机为 `/Users/arcdent/github/FORK/maibot-ForwardMessagesAuto-plugin/.venv/bin/python`）。
纯 Python 3.13 直接 discover 会在那六个模块上报 ModuleNotFoundError 或加载不到
`create_plugin`，那是缺 SDK，不是用例失败；逐个模块跑纯逻辑那九个则全绿。
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = str(Path(__file__).resolve().parent)

if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
