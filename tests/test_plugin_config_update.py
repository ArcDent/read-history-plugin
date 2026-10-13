"""任务 2.3：`on_config_update` 的 access 节签名比对与自重载测试。

用真实 maibot_sdk 的插件类，配一个记录调用的桩上下文（宿主就是这样把能力代理注入
插件的），验证：

- access 节变更触发恰好一次 `reload_plugin`，标识符是 manifest 的插件 id。
- 非 access 节变更、access 未变、全局范围广播都不触发重载。
- 重载失败（含非法 access 配置）只记日志、不抛未处理异常，已暴露工具的状态不翻转。
- 重载后 `allowed_session` 按新白名单重建。

容器内的实测（真实 config.toml 改动 + 宿主日志）在任务 5.2/5.3，本文件覆盖本机可
验证的取值与调用。
"""

from __future__ import annotations

import json
import logging
import unittest
from unittest import mock

from support import (
    PLUGIN_DIR,
    StubComponent,
    StubContext,
    StubDatabase,
    StubMessage,
    confirming_all,
    group_row,
    load_plugin_entry,
    load_plugin_module,
    make_plugin,
    message_dict,
    plugin_config,
    tool_components,
)

plugin_module = load_plugin_entry()
stream_resolver = load_plugin_module("stream_resolver")

TOOL_NAMES = plugin_module.TOOL_NAMES
GROUP_A = "1919810"
GROUP_B = "805457685"
SESSION_OF_GROUP_B = "00112233445566778899aabbccddeeff"


class ConfigUpdateTests(unittest.IsolatedAsyncioTestCase):
    """`on_config_update` 的接线与调度结果。"""

    def setUp(self) -> None:
        self.component = StubComponent()
        self.logger = logging.getLogger("read_history_plugin.tests.plugin")
        self.plugin = plugin_module.create_plugin()
        # 宿主就是这样注入上下文的（`_inject_context` → `instance._set_context(ctx)`）。
        self.plugin._set_context(StubContext(component=self.component, logger_name=self.logger.name))

    def _allowed_sessions(self) -> list[str]:
        with mock.patch.object(
            stream_resolver, "resolve_group_entries", side_effect=confirming_all
        ):
            components = tool_components(self.plugin, TOOL_NAMES)
        return list(components[TOOL_NAMES[0]]["allowed_session"])

    async def _load_with(self, config: dict) -> None:
        self.plugin.set_plugin_config(config)
        await self.plugin.on_load()

    async def test_access_change_triggers_exactly_one_reload(self) -> None:
        await self._load_with(plugin_config([GROUP_A]))
        await self.plugin.on_config_update("self", plugin_config([GROUP_A, GROUP_B]), "0.1.0")
        self.assertEqual(self.component.calls, [plugin_module.PLUGIN_ID])

    async def test_reload_identifier_is_the_manifest_id(self) -> None:
        """自重载传的是 manifest 的 id，不是插件目录名。

        上游三处注册键（`plugin_loader.py` 的 `plugin_id = manifest.id`、Supervisor 的
        `_registered_plugins`、`reload_plugins_globally` 的 Supervisor 映射）都用 manifest
        的 id；这里顺带断言它与目录名不同，避免以后有人按目录名改。
        """

        with (PLUGIN_DIR / "_manifest.json").open("r", encoding="utf-8") as manifest_file:
            manifest_id = json.load(manifest_file)["id"]
        self.assertNotEqual(manifest_id, PLUGIN_DIR.name)
        await self._load_with(plugin_config([GROUP_A]))
        await self.plugin.on_config_update("self", plugin_config([GROUP_B]), "0.1.0")
        self.assertEqual(self.component.calls, [manifest_id])
        self.assertEqual(manifest_id, "arcdent.read-history-plugin")

    async def test_private_switch_change_triggers_one_reload(self) -> None:
        await self._load_with(plugin_config([GROUP_A], private_enabled=False))
        await self.plugin.on_config_update("self", plugin_config([GROUP_A], private_enabled=True), "0.1.0")
        self.assertEqual(self.component.calls, [plugin_module.PLUGIN_ID])

    async def test_unchanged_access_does_not_reload(self) -> None:
        await self._load_with(plugin_config([GROUP_A]))
        await self.plugin.on_config_update("self", plugin_config([GROUP_A]), "0.1.0")
        self.assertEqual(self.component.calls, [])

    async def test_non_access_change_does_not_reload(self) -> None:
        await self._load_with(plugin_config([GROUP_A]))
        changed = plugin_config([GROUP_A])
        changed["recent"] = {"default_limit": 40, "max_limit": 50, "include_bot_messages": True}
        changed["summary"] = {"window_hours": 24, "max_input_messages": 100}
        await self.plugin.on_config_update("self", changed, "0.1.0")
        self.assertEqual(self.component.calls, [])

    async def test_whitelist_reorder_does_not_reload(self) -> None:
        """同一个群号集合换个书写顺序不算变化。"""

        await self._load_with(plugin_config([GROUP_A]))
        await self.plugin.on_config_update("self", plugin_config([" 1919810 "]), "0.1.0")
        self.assertEqual(self.component.calls, [])

    async def test_global_scope_update_does_not_reload(self) -> None:
        await self._load_with(plugin_config([GROUP_A]))
        await self.plugin.on_config_update("bot", plugin_config([GROUP_A, GROUP_B]), "0.1.0")
        self.assertEqual(self.component.calls, [])

    async def test_allowed_session_follows_new_whitelist_after_reload(self) -> None:
        await self._load_with(plugin_config([GROUP_A]))
        self.assertEqual(self._allowed_sessions(), [GROUP_A])
        self.plugin.set_plugin_config(plugin_config([GROUP_A, GROUP_B]))
        await self.plugin.on_config_update("self", plugin_config([GROUP_A, GROUP_B]), "0.1.0")
        self.assertEqual(self.component.calls, [plugin_module.PLUGIN_ID])
        self.assertEqual(self._allowed_sessions(), [GROUP_A, GROUP_B])


class ImmediateReadabilityAfterConfigSwapTests(unittest.IsolatedAsyncioTestCase):
    """spec「配置保存后校验立即生效」：读取校验直接读当前配置，换配置无需重载即生效。

    spec `chat-access-control` / Requirement「白名单与私聊开关变更后无需重启即恢复正确」
    的该场景：运维把一个新群加进白名单并保存配置后，随后对该群作为目标的读取调用立即
    按可读处理，不需要任何重载。这里不调用 `on_config_update`、不触发 `reload_plugin`，
    只把插件持有的配置换成新的一份，验证校验按新配置执行。
    """

    async def test_new_group_is_readable_right_after_config_swap_without_reload(self) -> None:
        ctx = StubContext(
            db=StubDatabase(
                [
                    group_row(GROUP_A),
                    group_row(GROUP_B, group_name="B群", session_id=SESSION_OF_GROUP_B),
                ]
            ),
            message=StubMessage(
                {SESSION_OF_GROUP_B: [message_dict(timestamp="1786000000.0", text="B群的历史消息")]}
            ),
        )
        plugin = make_plugin(plugin_config([GROUP_A]), ctx)

        before = await plugin.read_recent_messages(target_group_id=GROUP_B)
        self.assertIs(before["success"], False)
        self.assertIn("不可读", before["error"])

        # 运维把 B 加进白名单并保存配置：直接换插件持有的配置，不调用 on_config_update、不重载。
        plugin.set_plugin_config(plugin_config([GROUP_A, GROUP_B]))

        after = await plugin.read_recent_messages(target_group_id=GROUP_B)
        self.assertIs(after["success"], True)
        self.assertIn("B群的历史消息", after["content"])


class ConfigUpdateFailureTests(unittest.IsolatedAsyncioTestCase):
    """重载失败与非法 access 配置都不破坏现状。"""

    def setUp(self) -> None:
        self.logger = logging.getLogger("read_history_plugin.tests.plugin")
        self.plugin = plugin_module.create_plugin()

    def _install_context(self, component: StubComponent) -> None:
        self.plugin._set_context(StubContext(component=component, logger_name=self.logger.name))

    def _allowed_sessions(self) -> list[str]:
        with mock.patch.object(
            stream_resolver, "resolve_group_entries", side_effect=confirming_all
        ):
            components = tool_components(self.plugin, TOOL_NAMES)
        return list(components[TOOL_NAMES[0]]["allowed_session"])

    async def test_reload_failure_is_logged_and_swallowed(self) -> None:
        component = StubComponent(error=RuntimeError("宿主拒绝重载"))
        self._install_context(component)
        self.plugin.set_plugin_config(plugin_config([GROUP_A]))
        await self.plugin.on_load()

        with self.assertLogs(self.logger, level=logging.WARNING) as captured:
            await self.plugin.on_config_update("self", plugin_config([GROUP_A, GROUP_B]), "0.1.0")

        self.assertEqual(component.calls, [plugin_module.PLUGIN_ID])
        self.assertTrue(any("重载" in line for line in captured.output), captured.output)

    async def test_reload_failure_does_not_flip_exposed_groups(self) -> None:
        component = StubComponent(error=RuntimeError("宿主拒绝重载"))
        self._install_context(component)
        self.plugin.set_plugin_config(plugin_config([GROUP_A]))
        await self.plugin.on_load()
        before = self._allowed_sessions()

        with self.assertLogs(self.logger, level=logging.WARNING):
            await self.plugin.on_config_update("self", plugin_config([GROUP_A, GROUP_B]), "0.1.0")

        self.assertEqual(before, [GROUP_A])
        self.assertEqual(self._allowed_sessions(), [GROUP_A])

    async def test_invalid_access_config_does_not_flip_exposed_groups(self) -> None:
        """非法 access 配置：重载尝试失败被吞掉，已暴露的群维持原状。"""

        component = StubComponent(error=ValueError("白名单条目非法"))
        self._install_context(component)
        self.plugin.set_plugin_config(plugin_config([GROUP_A]))
        await self.plugin.on_load()
        before = self._allowed_sessions()

        invalid = plugin_config([GROUP_A])
        invalid["access"] = {"readable_streams": ["abc"], "private_enabled": False}
        with self.assertLogs(self.logger, level=logging.WARNING):
            await self.plugin.on_config_update("self", invalid, "0.1.0")

        self.assertEqual(component.calls, [plugin_module.PLUGIN_ID])
        self.assertEqual(before, [GROUP_A])
        self.assertEqual(self._allowed_sessions(), [GROUP_A])

    async def test_failed_reload_does_not_retry_on_the_same_config(self) -> None:
        """失败后签名照常推进，同一次配置不会反复触发重载。"""

        component = StubComponent(error=RuntimeError("宿主拒绝重载"))
        self._install_context(component)
        self.plugin.set_plugin_config(plugin_config([GROUP_A]))
        await self.plugin.on_load()

        with self.assertLogs(self.logger, level=logging.WARNING):
            await self.plugin.on_config_update("self", plugin_config([GROUP_B]), "0.1.0")
        await self.plugin.on_config_update("self", plugin_config([GROUP_B]), "0.1.0")

        self.assertEqual(len(component.calls), 1)


if __name__ == "__main__":
    unittest.main()
