"""插件组合根：插件类、三个 LLM 工具、组件暴露面覆写与配置热更新。

本模块是连接宿主与纯逻辑的胶水层：它 import maibot_sdk 并读强类型配置（`self.config`），
调用 `stream_resolver` 与 `history_reader` 里的宿主能力胶水。并非只有它 import SDK——
`config.py` 也用 SDK 的 `PluginConfigBase` / `Field` 声明配置模型。纯逻辑（可本机单测、
不 import maibot_sdk）在 `coercion.py`、`config_rules.py`、`access_gate.py`、
`target_params.py`、`history_reader.py` 的渲染与钳制部分、`summary_store.py` 与
`summary_generator.py`；
`stream_resolver.py`、`history_reader.fetch_recent_messages`、`summary_generator.generate_summary`
则含宿主能力调用，靠鸭子类型的 `ctx` 在测试里用桩驱动。

暴露面机制：宿主 Runner 只读组件声明的顶层 `chat_scope` 与 `allowed_session`，而
`@Tool(chat_scope=…)` 这类额外关键字参数经 SDK 只落进 metadata。因此三个工具的会话
范围必须在 `get_components()` 里提到顶层，否则群聊门禁不生效、私聊也会看到工具。

生命周期：SDK 基类的 `on_load` / `on_unload` / `on_config_update` 默认抛
NotImplementedError，Runner 把 `on_load` 失败当作激活失败并注销插件（上游
`runner_main.py` 的 `_invoke_plugin_on_load` → `_unregister_plugin(reason="on_load_failed")`），
所以这三个方法必须实现；`on_config_update` 用来在白名单或私聊开关变更后触发自重载。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

from maibot_sdk import MaiBotPlugin, Tool

from .access_gate import (
    CONFIG_RELOAD_SCOPE_SELF,
    AccessSignature,
    access_signature,
    group_is_readable,
    private_is_readable,
    readable_entries,
    reload_on_access_change,
)
from .config import ReadHistoryConfig
from .history_reader import (
    clamp_limit,
    fetch_recent_messages,
    render_history,
    render_no_messages,
    render_stream_list,
    render_summary,
)
from .stream_resolver import (
    ResolvedTarget,
    allowed_group_sessions,
    confirm_group_entries,
    fetch_group_sessions,
    fetch_known_group_ids,
    fetch_private_sessions,
    group_id_of,
    group_name_of,
    read_known_group_ids,
    resolve_target,
    set_known_group_ids,
    write_known_group_ids,
)
from .summary_generator import (
    NO_MESSAGES_REASON,
    STORE_UNAVAILABLE_REASON,
    build_summary_prompt,
    generate_summary,
    summary_failure_message,
    summary_input_start,
)
from .summary_store import (
    build_target_key,
    load_fresh_summary,
    read_summary,
    save_summary,
    summary_db_path,
)
from .target_params import (
    TARGET_TYPE_GROUP,
    Target,
    not_found_error,
    parse_target,
    unreadable_group_error,
    unreadable_private_error,
)

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)
"""模块日志：工具内部兜底异常时用，不依赖插件上下文是否已注入。"""

PLUGIN_ID: Final[str] = "arcdent.read-history-plugin"
"""插件 ID：与 `_manifest.json` 的 id 一致，自重载与日志定位用。"""

LIST_TOOL_NAME: Final[str] = "list_readable_streams"
"""列出可读聊天流的工具名。"""

SUMMARY_TOOL_NAME: Final[str] = "read_chat_summary"
"""读中期摘要的工具名。"""

RECENT_TOOL_NAME: Final[str] = "read_recent_messages"
"""读最近若干条原始消息的工具名。"""

TOOL_NAMES: Final[tuple[str, ...]] = (LIST_TOOL_NAME, SUMMARY_TOOL_NAME, RECENT_TOOL_NAME)
"""三个工具的名字清单：暴露面覆写按此清单统一判定，不逐个写死工具名。"""

PRIVATE_TARGET_LIMIT: Final[int] = 20
"""私聊目标清单的条数上限（代码常量）；超出时只列最近活跃的这些并在结果里说明截断。"""

GROUP_CHAT_SCOPE: Final[str] = "group"
"""工具的会话范围：私聊一律不暴露，私聊只作为被读取目标存在。"""

LIST_TOOL_PARAMETERS: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {},
    "required": [],
}
"""清单工具参数 JSON Schema：没有任何公开参数。"""

TARGET_ARGUMENT_PROPERTIES: Final[dict[str, Any]] = {
    "target_group_id": {
        "type": "string",
        "description": (
            "要读取的目标群号（字符串，避免长 ID 精度损失）；"
            "与 target_user_id 二选一，两者恰好提供一个。"
        ),
    },
    "target_user_id": {
        "type": "string",
        "description": (
            "要读取的私聊对端 QQ 号（字符串）；与 target_group_id 二选一，"
            "两者恰好提供一个，且私聊历史只在总闸打开时可读。"
        ),
    },
}
"""两个读取工具共用的目标参数：群号与 QQ 号二选一，都是字符串。"""

SUMMARY_TOOL_PARAMETERS: Final[dict[str, Any]] = {
    "type": "object",
    "properties": dict(TARGET_ARGUMENT_PROPERTIES),
    "required": [],
}
"""摘要工具参数 JSON Schema。"""

RECENT_TOOL_PARAMETERS: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        **TARGET_ARGUMENT_PROPERTIES,
        "limit": {
            "type": "integer",
            "description": (
                "想要读取的消息条数（可选）；不传按配置的默认条数，"
                "传得比配置上限大时按上限执行，不报错。"
            ),
        },
    },
    "required": [],
}
"""最近消息工具参数 JSON Schema。"""


class ReadHistoryPlugin(MaiBotPlugin):
    """插件主类：三个 LLM 工具、组件暴露面覆写与配置热更新。"""

    config_model = ReadHistoryConfig

    _access_signature: AccessSignature | None = None
    """access 节的签名基线：`on_load` 登记，`on_config_update` 比较后更新。"""

    # ------------------------------------------------------------------ 工具入口

    @Tool(
        LIST_TOOL_NAME,
        description=(
            "列出本插件允许读取的聊天流：每项给出目标类型（群聊或私聊）、群号或 QQ 号"
            "与名称。读取其它工具前先调用本工具确认合法目标；目标不在返回清单里时，"
            "读取工具会拒绝并说明原因。"
        ),
        parameters=LIST_TOOL_PARAMETERS,
        visibility="visible",
    )
    async def list_readable_streams(self, **kwargs: Any) -> dict[str, Any]:
        """列出当前可读的聊天流：群只列已确认为群的，私聊在开关打开时附上。

        群条目的确认来源有两处：本次查到的群会话行，以及上一次 `on_load` /
        `on_config_update` 落盘的群号快照（快照缺失时按空；`get_components()` 的暴露面
        用的是同一份快照）。白名单里写错的 QQ 号在宿主没有对应群会话，两处都确认不到，
        于是不作为群聊行出现——与 ADR-0003「白名单只装群号，写 QQ 号不产生作用」一致，
        避免 LLM 去读一个实际不可读的目标；这类条目的数量另用一行提示带出。
        """

        async def action() -> dict[str, Any]:
            access = self.config.access
            entries = list(access.readable_streams)
            name_by_group: dict[str, str] = {}
            for row in await fetch_group_sessions(self.ctx):
                group_id = group_id_of(row)
                if group_id and group_id not in name_by_group:
                    name_by_group[group_id] = group_name_of(row)
            known_groups = set(name_by_group) | set(read_known_group_ids(self._data_dir()))
            confirmed_entries = confirm_group_entries(entries, known_groups)
            unconfirmed_count = len(entries) - len(confirmed_entries)
            private_rows = await fetch_private_sessions(self.ctx) if access.private_enabled else []
            content = render_stream_list(
                confirmed_entries,
                name_by_group,
                private_rows,
                PRIVATE_TARGET_LIMIT,
                unconfirmed_count,
            )
            return {"success": True, "content": content}

        return await self._run_tool("列出可读聊天流", action)

    @Tool(
        SUMMARY_TOOL_NAME,
        description=(
            "读取指定聊天流的中期摘要，即该目标最近一段时间发生过什么的概括，"
            "用于回顾而不是查看当下原文。只接受 target_group_id 与 target_user_id "
            "二选一，均为字符串；目标不可读或暂无摘要时会明确说明，不会返回编造内容。"
        ),
        parameters=SUMMARY_TOOL_PARAMETERS,
        visibility="visible",
    )
    async def read_chat_summary(self, **kwargs: Any) -> dict[str, Any]:
        """读取目标的中期摘要：窗口内直接返回，否则当场概括并归档后返回。"""

        async def action() -> dict[str, Any]:
            resolved, error = await self._resolve_readable_target(kwargs)
            if resolved is None:
                return {"success": False, "error": error}
            return await self._summary_answer(resolved)

        return await self._run_tool("读取中期摘要", action)

    @Tool(
        RECENT_TOOL_NAME,
        description=(
            "读取指定聊天流最近的消息原文，结果含发送者与内容、按时间正序。"
            "只接受 target_group_id 与 target_user_id 二选一，均为字符串；"
            "limit 可选，超过配置上限时按上限执行。跨群读取意味着把目标聊天流的"
            "内容带进当前对话；可读范围是白名单内的群与私聊开关打开时的私聊。"
        ),
        parameters=RECENT_TOOL_PARAMETERS,
        visibility="visible",
    )
    async def read_recent_messages(self, **kwargs: Any) -> dict[str, Any]:
        """读取指定目标最近的若干条原始消息，按时间正序渲染。"""

        async def action() -> dict[str, Any]:
            resolved, error = await self._resolve_readable_target(kwargs)
            if resolved is None:
                return {"success": False, "error": error}
            recent = self.config.recent
            limit = clamp_limit(kwargs.get("limit"), recent.default_limit, recent.max_limit)
            messages = await fetch_recent_messages(
                self.ctx, resolved.session_id, limit, recent.include_bot_messages
            )
            if not messages:
                return {"success": True, "content": render_no_messages(resolved.name)}
            return {"success": True, "content": render_history(resolved.name, messages)}

        return await self._run_tool("读取最近消息", action)

    # ------------------------------------------------------------------ 工具共用流程

    async def _resolve_readable_target(
        self, arguments: Mapping[str, Any]
    ) -> tuple[ResolvedTarget | None, str]:
        """读取工具的前奏：参数解析 → 可读性判定 → 号到会话解析。

        返回 `(已解析目标, 空串)` 或 `(None, 可直接回给 LLM 的错误文案)`。三种失败
        （参数非法、目标不可读、目标不存在）在这一处统一处理，三个工具共用同一套
        文案与顺序，避免各写一份而彼此漂移。
        """

        target, argument_error = parse_target(arguments)
        if target is None:
            return None, argument_error
        readability_error = self._readability_error(target)
        if readability_error:
            return None, readability_error
        resolved = await resolve_target(
            self.ctx,
            target_type=target.target_type,
            target_id=target.target_id,
        )
        if resolved is None:
            return None, not_found_error(target)
        return resolved, ""

    async def _run_tool(
        self, label: str, action: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        """统一失败包装：任何未预期的异常都转成明确失败文案，不向外抛。"""

        try:
            return await action()
        except Exception:
            _LOGGER.warning("%s时出错", label, exc_info=True)
            return {"success": False, "error": f"{label}时出错，请稍后再试。"}

    async def _summary_answer(self, resolved: ResolvedTarget) -> dict[str, Any]:
        """摘要读取的主流程：新鲜即返回，否则概括、存储并返回。

        生成失败（模型不可用、返回不可用、范围内没有消息、存储写不进去）一律不落盘，
        旧摘要原样保留，并按「从未有摘要」或「旧摘要已过期」给出对应的告知文案。
        """

        summary = self.config.summary
        window_seconds = max(0, int(summary.window_hours) * 3600)
        max_input = max(1, int(summary.max_input_messages))
        now = time.time()
        db_path = summary_db_path(self._data_dir())
        key = build_target_key(resolved.target_type, resolved.target_id)

        fresh = load_fresh_summary(db_path, key, window_seconds, now)
        if fresh is not None:
            return {
                "success": True,
                "content": render_summary(resolved.name, fresh.text, fresh.cutoff_ts, fresh.created_ts),
            }

        previous = read_summary(db_path, key)
        previous_cutoff = previous.cutoff_ts if previous is not None else None
        start = summary_input_start(previous_cutoff, now, summary.window_hours)
        messages = await fetch_recent_messages(
            self.ctx,
            resolved.session_id,
            max_input,
            self.config.recent.include_bot_messages,
            start_time=start,
        )
        if not messages:
            return {
                "success": False,
                "error": summary_failure_message(resolved.name, NO_MESSAGES_REASON, previous_cutoff),
            }

        prompt = build_summary_prompt(resolved.name, messages, start, now)
        text, failure = await generate_summary(self.ctx, prompt)
        if failure:
            return {
                "success": False,
                "error": summary_failure_message(resolved.name, failure, previous_cutoff),
            }

        if not save_summary(
            db_path,
            target_key=key,
            target_type=resolved.target_type,
            text=text,
            cutoff_ts=now,
            created_ts=now,
        ):
            return {
                "success": False,
                "error": summary_failure_message(
                    resolved.name, STORE_UNAVAILABLE_REASON, previous_cutoff
                ),
            }
        return {"success": True, "content": render_summary(resolved.name, text, now, now)}

    def _readability_error(self, target: Target) -> str:
        """目标不可读时的文案；可读返回空串。

        群聊看白名单，私聊只看总闸，两条判定与门禁同源（`access_gate`）；配置用强类型
        `self.config.access` 读，不另写一层裸字典回落。
        """

        access = self.config.access
        if target.target_type == TARGET_TYPE_GROUP:
            if not group_is_readable(access.readable_streams, target.target_id):
                return unreadable_group_error(target.target_id)
            return ""
        if not private_is_readable(access.private_enabled):
            return unreadable_private_error()
        return ""

    # ------------------------------------------------------------------ 生命周期

    async def on_load(self) -> None:
        """加载完成：登记 access 节的基线签名，并把已知群号刷新进快照。

        快照供 `get_components()` 的暴露面确认使用（该方法同步、无法现查库）。本组首次
        加载时白名单可能已填好，而 `get_components()` 在 `on_load` 之前跑过，此时组件仍
        按空快照 fail-closed；管理员之后保存一次配置即触发自重载，届时快照已就位，新群
        在秒级窗口内看到工具。
        """

        self._access_signature = access_signature(self.get_plugin_config_data())
        await self._refresh_known_groups()

    async def on_unload(self) -> None:
        """卸载前：丢弃签名基线；本插件不持有其它需要释放的资源。"""

        self._access_signature = None

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """配置热更新：access 节变化时自重载，让暴露面按新白名单重建。

        比较与调度都在 `access_gate.reload_on_access_change`：非 `self` 范围、签名未变、
        首次调用都不触发重载；重载失败只记日志，已注册组件与暴露面保持重载前的状态。

        签名与触发时机已核对一手实现：本地 SDK 2.8.2（`maibot_sdk/plugin.py`）与容器用的
        SDK 2.12.0（PyPI wheel `maibot_plugin_sdk-2.12.0`，两版 `plugin.py` 逐字节相同）
        都声明 `async def on_config_update(self, scope: str, config_data: dict[str, Any],
        version: str) -> None`，基类实现直接抛 NotImplementedError，所以必须覆写；上游
        039b8c278 的 `runner_main.py::_handle_config_updated` 只在收到 `plugin.config_updated`
        RPC 时按位置传入这三个参数，加载时不调用，而且宿主不会自行重载插件，所以暴露面
        重建只能由这里发起自重载。
        """

        if scope == CONFIG_RELOAD_SCOPE_SELF:
            await self._refresh_known_groups()
        self._access_signature = await reload_on_access_change(
            scope=scope,
            config_data=config_data,
            previous_signature=self._access_signature,
            reload_plugin=self._reload_plugin,
            logger=self.ctx.logger,
        )

    async def _reload_plugin(self) -> None:
        """请求宿主重载本插件：标识符用 manifest 的插件 id。

        已核对上游 039b8c278 的一手实现：`component.reload_plugin` 能力把入参 `plugin_name`
        原样交给 `integration.py::reload_plugins_globally`；该方法先用
        `_build_registered_supervisor_map()`（键取自 `supervisor.get_loaded_plugin_ids()`，
        即已注册插件 ID）查找 Supervisor，未命中再走 `_find_supervisor_by_plugin_directory`，
        而后者用 `ManifestValidator.read_plugin_id_from_plugin_path` 读的仍是 manifest 声明的
        `id`。`plugin_loader.py` 里也是 `plugin_id = manifest.id`。三处键都是 manifest 的
        `id`，不是插件目录名；SDK 侧入参名 `plugin_name` 只是历史命名，传的必须是插件 id。
        所以这里传 `PLUGIN_ID`（`_manifest.json` 的 `id`），与目录名 `read_history_plugin`
        无关（有 test_plugin_config_update 断言两者不同、且传的是 id）。
        """

        await self.ctx.component.reload_plugin(PLUGIN_ID)

    # ------------------------------------------------------------------ 暴露面

    def get_components(self) -> list[dict[str, Any]]:
        """把三个工具的会话范围提到组件声明顶层，并按白名单设置暴露面。

        Runner 只读顶层 `chat_scope` 与 `allowed_session`（缺省分别回落 `"all"` 与
        空集合），而 SDK 采集组件时把额外关键字参数都塞进 metadata，所以这一层必须自己
        提上去，并把 metadata 里可能残留的同名键弹掉，避免两处取值不一致。按
        `TOOL_NAMES` 清单统一判定，三个工具同生共死。

        `allowed_session` 只装「已确认为群」的条目（见 `stream_resolver`）：白名单里的
        QQ 号与某个群的 group_id 数字碰撞时，直接塞条目会让非白名单群意外拿到工具。
        宿主语义里空集合等于「不做会话过滤」，所以集合为空时必须把三个组件显式置为
        禁用，否则白名单为空反而等于对所有群开放。
        """

        components = super().get_components()
        allowed_session = self._allowed_sessions()
        for component in components:
            if str(component.get("name") or "") not in TOOL_NAMES:
                continue
            component["chat_scope"] = GROUP_CHAT_SCOPE
            component["allowed_session"] = list(allowed_session)
            metadata = component.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                component["metadata"] = metadata
            metadata.pop("chat_scope", None)
            metadata.pop("allowed_session", None)
            metadata["enabled"] = bool(allowed_session)
        return components

    def _allowed_sessions(self) -> list[str]:
        """暴露面的白名单来源：配置白名单投影成「经会话表确认为群」的群号集合。

        `allowed_session` 的候选集是字符串集合，填群号即命中；但白名单条目都是纯数字串，
        格式上分不出群号与 QQ 号，写错的 QQ 号一旦撞上某个群的 group_id，那个非白名单群
        就会意外拿到工具。因此只采信经会话表确认过的群号（`stream_resolver`）。

        确认结果没法在 `get_components()` 里现查（同步方法，拿不到异步库查询），只能读
        上一次 `on_load` / `on_config_update` 落盘的群号快照：先把它刷进解析接缝的缓存，
        再按当前白名单投影。快照缺失或读不到时缓存为空，三个组件在新群上 fail-closed，
        要等一次配置变更或重载后的 `on_load` 落盘才恢复。

        群里可读的会话标识在后续工具内校验里另有判定，本方法只管暴露面。
        """

        set_known_group_ids(read_known_group_ids(self._data_dir()))
        return allowed_group_sessions(readable_entries(self.get_plugin_config_data()))

    def _data_dir(self) -> object:
        """插件 data_dir；上下文未注入或拿不到路径时返回 None。"""

        paths = getattr(self._ctx, "paths", None)
        return getattr(paths, "data_dir", None)

    async def _refresh_known_groups(self) -> None:
        """把当前宿主已知的全部群号写进快照，供下一次 `get_components()` 确认。

        拿不到 data_dir（上下文未注入）时静默返回，不查也不写；查询或写入失败只记日志、
        保留旧快照，加载路径不因此失败。
        """

        data_dir = self._data_dir()
        if data_dir is None:
            return
        try:
            known = await fetch_known_group_ids(self._ctx)
            write_known_group_ids(data_dir, known)
        except Exception:
            self.ctx.logger.warning(
                "刷新已知群号快照失败，暴露面维持上一次的确认结果；改配置或重启插件可重试",
                exc_info=True,
            )


def create_plugin() -> ReadHistoryPlugin:
    """宿主无参工厂：Runner 按此约定加载插件实例。"""

    return ReadHistoryPlugin()
