"""测试共用的定位、加载助手与宿主能力桩。

本模块不属于被测试的插件代码。三块内容：仓库根、插件目录与「按宿主的加载形态导入
插件模块」的定位能力；`ctx` 各能力的桩（db / chat / message / llm / component / paths）；
以及被多个测试文件共用的夹具（常量、`plugin_config` / `make_plugin` / 会话行构造等），
避免每个测试文件各写一份。

宿主以 `spec_from_file_location(entry, plugin.py, submodule_search_locations=[插件目录])`
的方式把 `plugin.py` 当成包根加载，于是 `plugin.py` 里的 `from .config import …` 解析
为「同一插件目录下的 config.py」。本地测试复现同一形态：`load_plugin_entry()` 把
`plugin.py` 加载为包 `read_history_plugin`，`load_plugin_module()` 加载同一目录下的
子模块。两条路径共用同一个包与同一份子模块对象，测试里拿到的配置模型与插件类持有的
是同一个类，`is` 比较才有意义。
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
"""仓库根目录。"""

PLUGIN_DIR = REPO_ROOT / "plugins" / "read_history_plugin"
"""插件目录：宿主挂载到 MaiBot 的 plugins/ 下的那一份内容。"""

PLUGIN_PACKAGE = "read_history_plugin"
"""插件目录在测试进程里对应的包名：插件模块之间的相对导入靠它解析。"""

PLUGIN_ENTRY = "plugin.py"
"""宿主的单文件入口名。"""


def _register(module: ModuleType) -> ModuleType:
    """把模块登记进 `sys.modules` 并返回它。"""

    sys.modules[module.__name__] = module
    return module


def install_plugin_package() -> ModuleType:
    """保证 `sys.modules` 里有名为 `read_history_plugin` 的包（含 `__path__`）。

    插件目录没有 `__init__.py`（宿主以单文件入口加载，不需要它），所以纯模块测试先
    手工造一个只有 `__path__` 的包模块当相对导入的锚点；`load_plugin_entry()` 会把
    这个占位锚点换成真实的 `plugin.py` 模块。
    """

    package = sys.modules.get(PLUGIN_PACKAGE)
    if package is None or not hasattr(package, "__path__"):
        package = ModuleType(PLUGIN_PACKAGE)
        package.__path__ = [str(PLUGIN_DIR)]  # type: ignore[attr-defined]
        package.__plugin_placeholder__ = True  # type: ignore[attr-defined]
        sys.modules[PLUGIN_PACKAGE] = package
    return package


def _load(full_name: str, file_name: str, *, as_package: bool) -> ModuleType:
    """按文件路径加载一个模块；`as_package` 为真时同时给出子模块搜索路径。"""

    existing = sys.modules.get(full_name)
    if existing is not None:
        return existing

    target = PLUGIN_DIR / file_name
    spec = importlib.util.spec_from_file_location(
        full_name,
        target,
        submodule_search_locations=[str(PLUGIN_DIR)] if as_package else None,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法为 {target.name} 创建模块 spec")

    module = importlib.util.module_from_spec(spec)
    _register(module)
    spec.loader.exec_module(module)
    return module


def load_plugin_entry() -> ModuleType:
    """按宿主的方式把 `plugin.py` 加载为包根 `read_history_plugin`。"""

    package = install_plugin_package()
    if getattr(package, "__plugin_placeholder__", False):
        # 先把占位锚点摘掉，_load 才会真的去加载 plugin.py。
        sys.modules.pop(PLUGIN_PACKAGE, None)
    return _load(PLUGIN_PACKAGE, PLUGIN_ENTRY, as_package=True)


def load_plugin_module(module_name: str, file_name: str = "") -> ModuleType:
    """加载插件目录下的子模块 `read_history_plugin.<module_name>`。

    Args:
        module_name: 不带包前缀的模块名，例如 `"stream_resolver"`。
        file_name: 可选的文件名；缺省为 `<module_name>.py`。
    """

    install_plugin_package()
    return _load(
        f"{PLUGIN_PACKAGE}.{module_name}",
        file_name or f"{module_name}.py",
        as_package=False,
    )


# --------------------------------------------------------------------- 宿主能力桩


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _message_user_id(message: dict[str, Any]) -> str:
    info = message.get("message_info")
    user = info.get("user_info") if isinstance(info, dict) else None
    value = user.get("user_id") if isinstance(user, dict) else None
    return str(value) if value is not None else ""


class StubPaths:
    """`ctx.paths` 的桩：只暴露 data_dir。"""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir if data_dir is not None else Path("/nonexistent/read-history-plugin")
        self.runtime_dir = self.data_dir


class StubDatabase:
    """`ctx.db` 的桩：按宿主的 `database.get` 语义过滤、排序、限条并返回行清单。

    宿主侧 `db_get` 对 `ChatSession` 的 `last_active_timestamp` 默认升序、返回序列化后的
    行字典（时间为 ISO 字符串）；`single_result` 时返回单行。桩复现这一契约，让本机
    测试与宿主同形。
    """

    def __init__(self, rows: list[dict[str, Any]] | None = None, error: Exception | None = None) -> None:
        self.rows = list(rows or [])
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def get(
        self,
        model_name: str,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        order_by: str | list[str] | None = None,
        single_result: bool = False,
    ) -> Any:
        self.calls.append(
            {
                "model_name": model_name,
                "filters": dict(filters or {}),
                "limit": limit,
                "order_by": order_by,
                "single_result": single_result,
            }
        )
        if self.error is not None:
            raise self.error
        rows = [row for row in self.rows if _row_matches(row, filters or {})]
        rows = _apply_order_by(rows, order_by)
        if limit:
            rows = rows[:limit]
        if single_result:
            return rows[0] if rows else None
        return rows


def _row_matches(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    return all(row.get(key) == value for key, value in filters.items())


def _apply_order_by(rows: list[dict[str, Any]], order_by: str | list[str] | None) -> list[dict[str, Any]]:
    if not order_by:
        return rows
    fields = [order_by] if isinstance(order_by, str) else list(order_by)
    ordered = list(rows)
    for field in reversed(fields):
        descending = field.startswith("-")
        name = field[1:] if descending else field
        ordered.sort(key=lambda row: _sort_value(row.get(name)), reverse=descending)
    return ordered


def _sort_value(value: Any) -> tuple[int, Any]:
    if value is None:
        return (1, "")
    return (0, value)


class StubChat:
    """`ctx.chat` 的桩：群/私聊流清单与按号查找，返回宿主解包后的 stream 结构或 None。"""

    def __init__(
        self,
        group_streams: list[dict[str, Any]] | None = None,
        private_streams: list[dict[str, Any]] | None = None,
        stream_by_group: dict[str, dict[str, Any]] | None = None,
        stream_by_user: dict[str, dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.group_streams = list(group_streams or [])
        self.private_streams = list(private_streams or [])
        self.stream_by_group = dict(stream_by_group or {})
        self.stream_by_user = dict(stream_by_user or {})
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def get_group_streams(self, platform: str = "qq") -> list[dict[str, Any]]:
        self.calls.append(("get_group_streams", platform))
        if self.error is not None:
            raise self.error
        return list(self.group_streams)

    async def get_private_streams(self, platform: str = "qq") -> list[dict[str, Any]]:
        self.calls.append(("get_private_streams", platform))
        if self.error is not None:
            raise self.error
        return list(self.private_streams)

    async def get_stream_by_group_id(self, group_id: str, platform: str = "qq") -> dict[str, Any] | None:
        self.calls.append(("get_stream_by_group_id", group_id))
        if self.error is not None:
            raise self.error
        return self.stream_by_group.get(str(group_id))

    async def get_stream_by_user_id(self, user_id: str, platform: str = "qq") -> dict[str, Any] | None:
        self.calls.append(("get_stream_by_user_id", user_id))
        if self.error is not None:
            raise self.error
        return self.stream_by_user.get(str(user_id))


class StubLLM:
    """`ctx.llm` 的桩：按宿主真实签名记录调用，并返回罐头概括结果。

    签名与 `maibot_sdk.capabilities.llm.LLMCapability.generate` 对齐：位置参数
    `prompt` / `model` / `temperature` / `max_tokens`，关键字参数 `task_name` /
    `model_name`。宿主把结果里的 `model_name` 归一成 `model`（`_normalize_llm_result`），
    所以默认结果的模型键用 `model`。成功时 `response` 是可用的概括文本；失败时
    `success=False` 且 `response` 里是错误文本（宿主异常路径还带 `error`）。测试用
    `result=` 覆盖成失败结构、用 `error=` 让调用本身抛异常。
    """

    def __init__(self, result: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.result = (
            dict(result)
            if result is not None
            else {"success": True, "response": "这是概括文本", "reasoning": "", "model": "planner-model"}
        )
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        prompt: Any,
        model: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        task_name: str | None = None,
        model_name: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "prompt": prompt,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "task_name": task_name,
                "model_name": model_name,
                **kwargs,
            }
        )
        if self.error is not None:
            raise self.error
        return dict(self.result)


class StubComponent:
    """`ctx.component` 的桩：记录 `reload_plugin` 调用，可按需让它抛异常。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[str] = []

    async def reload_plugin(self, plugin_name: str) -> dict[str, Any]:
        self.calls.append(plugin_name)
        if self.error is not None:
            raise self.error
        return {"success": True}


class StubMessage:
    """`ctx.message` 的桩：按会话返回罐头消息，并复现宿主的过滤与限条语义。

    宿主 `get_by_time_in_chat` 会按时间窗过滤、按 `filter_command` / `filter_mai` 过滤，
    并在 `limit_mode="latest"` 时取最新 N 条后按时间正序返回。桩复现这些行为，好让测试
    能真正验证插件传对了参数（例如宽时间窗与过滤开关）。
    """

    def __init__(
        self,
        messages_by_chat: dict[str, list[dict[str, Any]]] | None = None,
        bot_ids: set[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.messages_by_chat = dict(messages_by_chat or {})
        self.bot_ids = set(bot_ids or [])
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def get_by_time_in_chat(
        self, chat_id: str, start_time: Any, end_time: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        self.calls.append({"chat_id": chat_id, "start_time": start_time, "end_time": end_time, **kwargs})
        if self.error is not None:
            raise self.error
        messages = [dict(message) for message in self.messages_by_chat.get(chat_id, [])]
        start = _to_float(start_time)
        end = _to_float(end_time)
        filter_command = kwargs.get("filter_command", False) is True
        filter_mai = kwargs.get("filter_mai", False) is True
        limit = kwargs.get("limit", 0)
        limit_mode = kwargs.get("limit_mode", "latest")

        kept: list[dict[str, Any]] = []
        for message in messages:
            timestamp = _to_float(message.get("timestamp"))
            if start is not None and timestamp is not None and timestamp < start:
                continue
            if end is not None and timestamp is not None and timestamp > end:
                continue
            if filter_command and message.get("is_command") is True:
                continue
            if filter_mai and _message_user_id(message) in self.bot_ids:
                continue
            kept.append(message)

        kept.sort(key=lambda message: _to_float(message.get("timestamp")) or 0.0)
        if isinstance(limit, int) and limit > 0 and limit_mode == "latest":
            kept = kept[-limit:]
        return kept


class StubContext:
    """宿主注入的通用的最小上下文桩：db / chat / message / llm / component / logger / paths。"""

    def __init__(
        self,
        *,
        db: StubDatabase | None = None,
        chat: StubChat | None = None,
        message: StubMessage | None = None,
        llm: StubLLM | None = None,
        component: StubComponent | None = None,
        data_dir: Path | None = None,
        logger_name: str = "read_history_plugin.tests.stub",
    ) -> None:
        self.db = db if db is not None else StubDatabase()
        self.chat = chat if chat is not None else StubChat()
        self.message = message if message is not None else StubMessage()
        self.llm = llm if llm is not None else StubLLM()
        self.component = component if component is not None else StubComponent()
        self.paths = StubPaths(data_dir)
        self.logger = logging.getLogger(logger_name)


def session_row(
    session_id: str,
    *,
    group_id: str | None = None,
    group_name: str | None = None,
    user_id: str | None = None,
    user_nickname: str | None = None,
    user_cardname: str | None = None,
    last_active_timestamp: str | None = None,
    platform: str = "qq",
) -> dict[str, Any]:
    """构造一行形如宿主 `database.get("ChatSession")` 返回的会话行。"""

    return {
        "session_id": session_id,
        "stream_id": session_id,
        "group_id": group_id,
        "group_name": group_name,
        "user_id": user_id,
        "user_nickname": user_nickname,
        "user_cardname": user_cardname,
        "last_active_timestamp": last_active_timestamp,
        "platform": platform,
    }


def message_dict(
    *,
    timestamp: str,
    text: str,
    user_id: str = "10001",
    nickname: str = "群友",
    cardname: str | None = None,
    message_id: str = "m1",
    is_command: bool = False,
    group_id: str = "1919810",
    group_name: str = "测试群",
) -> dict[str, Any]:
    """构造一条形如宿主 `message.get_by_time_in_chat` 返回的消息字典。"""

    return {
        "message_id": message_id,
        "timestamp": timestamp,
        "platform": "qq",
        "message_info": {
            "user_info": {"user_id": user_id, "user_nickname": nickname, "user_cardname": cardname},
            "group_info": {"group_id": group_id, "group_name": group_name},
        },
        "raw_message": {},
        "is_mentioned": False,
        "is_at": False,
        "is_emoji": False,
        "is_picture": False,
        "is_command": is_command,
        "is_notify": False,
        "session_id": "sess",
        "reply_to": None,
        "processed_plain_text": text,
    }


# --------------------------------------------------------------------- 插件测试夹具

GROUP = "1919810"
"""测试用白名单群号，与 spec 场景字面量一致。"""

OTHER_GROUP = "805457685"
"""测试用白名单外的另一个群号。"""

PRIVATE_PEER = "10001"
"""测试用私聊对端 QQ 号。"""

GROUP_NAME = "测试群"
"""`GROUP` 的群名。"""

SESSION_OF_GROUP = "eef21eda523391a356f8c60efcf1c493"
"""`GROUP` 对应的宿主 session_id。"""

SESSION_OF_PEER = "ffeeddccbbaa99887766554433221100"
"""`PRIVATE_PEER` 对应的宿主 session_id。"""


def plugin_config(
    readable_streams: list[str],
    private_enabled: bool = False,
    default_limit: int = 20,
    max_limit: int = 50,
    include_bot_messages: bool = False,
    window_hours: int = 6,
    max_input_messages: int = 200,
) -> dict[str, Any]:
    """构造一份完整插件配置字典（`set_plugin_config` 要求含 config_version）。"""

    return {
        "plugin": {"enabled": True, "config_version": "0.1.0"},
        "access": {"readable_streams": list(readable_streams), "private_enabled": private_enabled},
        "recent": {
            "default_limit": default_limit,
            "max_limit": max_limit,
            "include_bot_messages": include_bot_messages,
        },
        "summary": {"window_hours": window_hours, "max_input_messages": max_input_messages},
    }


def make_plugin(config: dict[str, Any], ctx: StubContext) -> Any:
    """按宿主的加载形态创建插件实例、注入配置与上下文，返回插件对象。"""

    plugin = load_plugin_entry().create_plugin()
    plugin.set_plugin_config(config)
    plugin._set_context(ctx)
    return plugin


def group_row(
    group_id: str = GROUP,
    group_name: str = GROUP_NAME,
    session_id: str = SESSION_OF_GROUP,
    last_active: str = "2026-10-12T15:19:00",
) -> dict[str, Any]:
    """构造一行群会话行。"""

    return session_row(
        session_id, group_id=group_id, group_name=group_name, last_active_timestamp=last_active
    )


def private_row(
    user_id: str = PRIVATE_PEER,
    nickname: str = "小明",
    session_id: str = SESSION_OF_PEER,
    last_active: str = "2026-10-12T15:19:00",
) -> dict[str, Any]:
    """构造一行私聊会话行。"""

    return session_row(
        session_id, user_id=user_id, user_nickname=nickname, last_active_timestamp=last_active
    )


def confirming_all(entries: list[str]) -> list[str]:
    """桩解析器：把白名单里的每个条目都当作已确认的群。"""

    return list(entries)


def tool_components(plugin: Any, tool_names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """插件的 Tool 组件声明，按名字索引（只取 `tool_names` 里的那些）。"""

    return {
        component["name"]: component
        for component in plugin.get_components()
        if component["name"] in tool_names
    }
