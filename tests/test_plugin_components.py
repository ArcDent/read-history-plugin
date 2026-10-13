"""任务 2.2：`get_components()` 提升顶层 `chat_scope` / `allowed_session` 的行为测试。

用真实 maibot_sdk 构造插件实例、注入配置、跑真实的 `get_components()`，再断言：

- 三个 Tool 的顶层 `chat_scope` 是 `group`、`allowed_session` 等于白名单里的群号集合。
- metadata 里没有残留的 `chat_scope` / `allowed_session` 同名键。
- 解析接缝没确认出任何群时，三个组件都置 `metadata.enabled=false`（fail-closed）。
- 三个工具同生共死：要么都在暴露面里，要么都不在。

宿主侧的最终确认要用容器内的 `check_component_enabled`（任务 5.2）。本文件把上游
`check_component_enabled`（MaiBot commit 039b8c278，容器内 component_registry.py 的
md5 为 89ba30ca656b48ef52ef1adb7833a113）的判定顺序复刻成一个本地 oracle，用它验证
「白名单群放行、白名单外群拒绝、私聊会话拒绝」这三条结论来自插件声明的取值。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from support import (
    StubContext,
    StubDatabase,
    confirming_all,
    load_plugin_entry,
    load_plugin_module,
    plugin_config,
    session_row,
    tool_components,
)

plugin_module = load_plugin_entry()
stream_resolver = load_plugin_module("stream_resolver")

TOOL_NAMES = plugin_module.TOOL_NAMES
"""三个工具的名字（按被测试模块的清单，不另写一份字面量）。"""

WHITELISTED_GROUP = "1919810"
"""白名单里那个群的群号，与 spec 场景字面量一致。"""

OTHER_GROUP = "805457685"
"""白名单外的群号。"""

PRIVATE_PEER = "10001"
"""一个 QQ 号，用来验证私聊会话拿不到工具。"""


def host_component_enabled(
    *,
    chat_scope: str,
    allowed_session: set[str],
    enabled: bool,
    session_id: str = "",
    is_group_chat: bool | None = None,
    group_id: str = "",
    platform: str = "",
) -> bool:
    """复刻上游 `check_component_enabled` 的判定顺序，作为契约 oracle。

    真实宿主由任务 5.2 在容器内确认；这里只验证插件声明的取值喂进同一套规则后能得到
    spec 要求的结果。
    """

    if is_group_chat is not None:
        if chat_scope == "group" and is_group_chat is not True:
            return False
        if chat_scope == "private" and is_group_chat is not False:
            return False
    if allowed_session:
        candidates = {str(session_id or "").strip(), str(group_id or "").strip()}
        if platform and group_id:
            candidates.add(f"{platform}:{group_id}")
        candidates.discard("")
        if allowed_session.isdisjoint(candidates):
            return False
    return enabled


def tool_declarations(plugin: Any) -> list[dict[str, Any]]:
    """把三个 Tool 的暴露面取值整理成喂给宿主判定用的声明清单。"""

    return [
        {
            "chat_scope": component["chat_scope"],
            "allowed_session": set(component["allowed_session"]),
            "enabled": component["metadata"].get("enabled", True) is not False,
        }
        for component in tool_components(plugin, TOOL_NAMES).values()
    ]


class ComponentDeclarationTests(unittest.TestCase):
    """组件声明本身的取值。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.plugin = plugin_module.create_plugin()
        cls.plugin.set_plugin_config(plugin_config([WHITELISTED_GROUP]))
        with mock.patch.object(stream_resolver, "resolve_group_entries", side_effect=confirming_all):
            cls.components = tool_components(cls.plugin, TOOL_NAMES)

    def test_all_three_tools_are_present(self) -> None:
        self.assertEqual(set(self.components), set(TOOL_NAMES))

    def test_chat_scope_is_group_for_every_tool(self) -> None:
        for name in TOOL_NAMES:
            self.assertEqual(self.components[name]["chat_scope"], "group", name)

    def test_allowed_session_equals_whitelisted_group_set(self) -> None:
        for name in TOOL_NAMES:
            self.assertEqual(self.components[name]["allowed_session"], [WHITELISTED_GROUP], name)

    def test_metadata_has_no_duplicate_session_keys(self) -> None:
        for name in TOOL_NAMES:
            metadata = self.components[name]["metadata"]
            self.assertNotIn("chat_scope", metadata, name)
            self.assertNotIn("allowed_session", metadata, name)

    def test_tools_stay_enabled_when_a_group_is_confirmed(self) -> None:
        for name in TOOL_NAMES:
            self.assertIsNot(self.components[name]["metadata"].get("enabled"), False, name)


class FailClosedTests(unittest.TestCase):
    """解析接缝确认不出任何群时，三个组件全部 fail-closed。"""

    def setUp(self) -> None:
        self.plugin = plugin_module.create_plugin()

    def test_empty_whitelist_disables_all_tools(self) -> None:
        self.plugin.set_plugin_config(plugin_config([]))
        components = tool_components(self.plugin, TOOL_NAMES)
        for name in TOOL_NAMES:
            self.assertEqual(components[name]["allowed_session"], [], name)
            self.assertIs(components[name]["metadata"]["enabled"], False, name)

    def test_unconfirmed_whitelists_stay_unexposed(self) -> None:
        """没有快照确认时，无论是群号还是 QQ 号条目，暴露面都保持为空。"""

        for group_number_whitelist in ([WHITELISTED_GROUP], [PRIVATE_PEER]):
            self.plugin.set_plugin_config(plugin_config(group_number_whitelist, private_enabled=True))
            components = tool_components(self.plugin, TOOL_NAMES)
            for name in TOOL_NAMES:
                self.assertEqual(components[name]["allowed_session"], [], name)
                self.assertIs(components[name]["metadata"]["enabled"], False, name)


class MetadataShapeTests(unittest.TestCase):
    """评审意见：metadata 不是 dict 时也要走禁用判定，不留空 allowed_session 的无标记组件。"""

    def _plugin(self) -> Any:
        plugin = plugin_module.create_plugin()
        plugin.set_plugin_config(plugin_config([]))
        return plugin

    def test_non_dict_metadata_is_replaced_and_disabled(self) -> None:
        plugin = self._plugin()

        def fake_super_components(self: Any) -> list[dict[str, Any]]:
            return [
                {"name": name, "type": "TOOL", "metadata": None} for name in TOOL_NAMES
            ]

        with mock.patch.object(plugin_module.MaiBotPlugin, "get_components", fake_super_components):
            components = tool_components(plugin, TOOL_NAMES)

        for name in TOOL_NAMES:
            self.assertEqual(components[name]["allowed_session"], [], name)
            self.assertIsInstance(components[name]["metadata"], dict, name)
            self.assertIs(components[name]["metadata"]["enabled"], False, name)


class HostGateReplicaTests(unittest.TestCase):
    """把插件声明的取值喂进上游判定顺序，验证 spec 的三条暴露结论。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.plugin = plugin_module.create_plugin()
        cls.plugin.set_plugin_config(plugin_config([WHITELISTED_GROUP]))
        with mock.patch.object(stream_resolver, "resolve_group_entries", side_effect=confirming_all):
            cls.declarations = tool_declarations(cls.plugin)

    def _all_tools_enabled(self, **context: Any) -> bool:
        return all(host_component_enabled(**declaration, **context) for declaration in self.declarations)

    def test_whitelisted_group_is_allowed(self) -> None:
        self.assertTrue(
            self._all_tools_enabled(
                session_id="eef21eda523391a356f8c60efcf1c493",
                is_group_chat=True,
                group_id=WHITELISTED_GROUP,
                platform="qq",
            )
        )

    def test_group_outside_whitelist_is_denied(self) -> None:
        self.assertFalse(
            self._all_tools_enabled(
                session_id="00112233445566778899aabbccddeeff",
                is_group_chat=True,
                group_id=OTHER_GROUP,
                platform="qq",
            )
        )

    def test_private_chat_is_denied(self) -> None:
        self.assertFalse(
            self._all_tools_enabled(
                session_id="ffeeddccbbaa99887766554433221100",
                is_group_chat=False,
                group_id="",
                platform="qq",
            )
        )

    def test_private_whitelist_entry_does_not_open_private_chat(self) -> None:
        """白名单里写 QQ 号不改变私聊的暴露结果，有没有这个条目都一样。"""

        private_context = {
            "session_id": "ffeeddccbbaa99887766554433221100",
            "is_group_chat": False,
            "group_id": "",
            "platform": "qq",
        }

        def private_is_exposed(readable_streams: list[str]) -> bool:
            plugin = plugin_module.create_plugin()
            plugin.set_plugin_config(plugin_config(readable_streams, private_enabled=True))
            with mock.patch.object(
                stream_resolver, "resolve_group_entries", side_effect=confirming_all
            ):
                declarations = tool_declarations(plugin)
            return all(
                host_component_enabled(**declaration, **private_context) for declaration in declarations
            )

        without_entry = private_is_exposed([WHITELISTED_GROUP])
        with_entry = private_is_exposed([WHITELISTED_GROUP, PRIVATE_PEER])
        self.assertFalse(with_entry)
        self.assertEqual(without_entry, with_entry)


class SnapshotExposureTests(unittest.IsolatedAsyncioTestCase):
    """群号快照驱动的暴露面：`get_components()` 从 data_dir 的快照确认群号。"""

    SESSION = "eef21eda523391a356f8c60efcf1c493"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(stream_resolver.set_known_group_ids, ())
        self.data_dir = Path(self._tmp.name)

    def _plugin(self, readable_streams: list[str]) -> Any:
        plugin = plugin_module.create_plugin()
        plugin.set_plugin_config(plugin_config(readable_streams))
        plugin._set_context(StubContext(data_dir=self.data_dir))
        return plugin

    def test_snapshot_group_is_exposed_without_patching(self) -> None:
        stream_resolver.write_known_group_ids(self.data_dir, [WHITELISTED_GROUP])
        components = tool_components(self._plugin([WHITELISTED_GROUP]), TOOL_NAMES)
        for name in TOOL_NAMES:
            self.assertEqual(components[name]["allowed_session"], [WHITELISTED_GROUP], name)

    def test_whitelist_entry_absent_from_snapshot_stays_unexposed(self) -> None:
        """快照里没有的条目（例如写错的 QQ 号）不放行。"""

        stream_resolver.write_known_group_ids(self.data_dir, [WHITELISTED_GROUP])
        components = tool_components(self._plugin([OTHER_GROUP]), TOOL_NAMES)
        for name in TOOL_NAMES:
            self.assertEqual(components[name]["allowed_session"], [], name)
            self.assertIs(components[name]["metadata"]["enabled"], False, name)

    async def test_on_load_writes_snapshot_from_database(self) -> None:
        ctx = StubContext(
            db=StubDatabase(
                [
                    session_row(self.SESSION, group_id=WHITELISTED_GROUP,
                                last_active_timestamp="2026-10-12T15:19:00"),
                    session_row("ffeeddccbbaa99887766554433221100", user_id=PRIVATE_PEER,
                                last_active_timestamp="2026-10-12T15:19:00"),
                ]
            ),
            data_dir=self.data_dir,
        )
        plugin = plugin_module.create_plugin()
        plugin.set_plugin_config(plugin_config([]))
        plugin._set_context(ctx)
        await plugin.on_load()
        self.assertEqual(stream_resolver.read_known_group_ids(self.data_dir), [WHITELISTED_GROUP])


if __name__ == "__main__":
    unittest.main()
