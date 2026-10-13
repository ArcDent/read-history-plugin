"""`plugin.py` 的工具声明与语义测试（用真实 maibot_sdk，本机 venv 可跑）。

组 1 评审指出上一版约 70 行手写 AST 解释器既脆弱又只在测写法。这一版直接导入真实
SDK，断言真实的对象与语义：

- 三个工具都是 `maibot_sdk` 采集得到的真实组件声明，且显式 `visibility="visible"`。
- 由 SDK 生成的参数 Schema 里 `required` 为空、清单工具没有参数、两个读取工具的
  目标参数与 `limit` 都在。
- 工具方法接收任意关键字参数（宿主按 `handler_method(**invoke.args)` 调用），不要求
  形参名字面等于 `kwargs`。
- 插件类的 `config_model` 指向 `ReadHistoryConfig`，SDK 侧能识别。
- SDK 采集出的组件声明**没有**顶层 `chat_scope` / `allowed_session`，这正是
  `get_components()` 必须覆写提升的原因。
"""

from __future__ import annotations

import inspect
import json
import unittest

from maibot_sdk.components import collect_components

from support import PLUGIN_DIR, load_plugin_entry, load_plugin_module

plugin_module = load_plugin_entry()
config_module = load_plugin_module("config")

EXPECTED_TOOL_NAMES = ("list_readable_streams", "read_chat_summary", "read_recent_messages")
"""三个工具的名字，与 tasks.md 任务 1.3 的字面量一致。"""

TARGET_ARGUMENTS = ("target_group_id", "target_user_id")
"""两个读取工具共用的目标参数名。"""

COMPONENT_INFO_ATTRIBUTE = "__maibot_component_info__"
"""SDK 把组件声明附着在处理器函数上的属性名（`maibot_sdk.components` 采集时读它）。"""


class ToolDeclarationTests(unittest.TestCase):
    """三个工具的真实组件声明。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.plugin = plugin_module.create_plugin()
        cls.components = {component["name"]: component for component in collect_components(cls.plugin)}
        cls.tool_infos = {
            name: getattr(getattr(cls.plugin, name), COMPONENT_INFO_ATTRIBUTE)
            for name in EXPECTED_TOOL_NAMES
        }

    def test_exactly_three_tools_are_declared(self) -> None:
        self.assertEqual(set(self.components), set(EXPECTED_TOOL_NAMES))

    def test_every_tool_is_a_tool_component(self) -> None:
        for name in EXPECTED_TOOL_NAMES:
            self.assertEqual(self.components[name]["type"], "TOOL", name)

    def test_every_tool_is_explicitly_visible(self) -> None:
        for name in EXPECTED_TOOL_NAMES:
            self.assertEqual(self.components[name]["metadata"].get("visibility"), "visible", name)

    def test_every_tool_has_non_empty_description(self) -> None:
        for name in EXPECTED_TOOL_NAMES:
            self.assertTrue(str(self.components[name]["metadata"].get("description", "")).strip(), name)

    def test_every_tool_schema_has_empty_required(self) -> None:
        for name in EXPECTED_TOOL_NAMES:
            schema = self.tool_infos[name].get_parameters_schema()
            self.assertEqual(schema["required"], [], name)

    def test_list_tool_declares_no_arguments(self) -> None:
        schema = self.tool_infos["list_readable_streams"].get_parameters_schema()
        self.assertEqual(schema["properties"], {})

    def test_two_reading_tools_declare_both_target_arguments(self) -> None:
        for name in ("read_chat_summary", "read_recent_messages"):
            properties = self.tool_infos[name].get_parameters_schema()["properties"]
            for argument in TARGET_ARGUMENTS:
                self.assertIn(argument, properties, f"{name} 缺少 {argument}")

    def test_recent_tool_declares_optional_limit(self) -> None:
        properties = self.tool_infos["read_recent_messages"].get_parameters_schema()["properties"]
        self.assertIn("limit", properties)

    def test_every_handler_accepts_arbitrary_keyword_arguments(self) -> None:
        """宿主按 `handler_method(**invoke.args)` 调用，形参名不作要求。"""

        for name in EXPECTED_TOOL_NAMES:
            signature = inspect.signature(getattr(self.plugin, name))
            kinds = {parameter.kind for parameter in signature.parameters.values()}
            self.assertIn(inspect.Parameter.VAR_KEYWORD, kinds, name)

    def test_every_handler_is_a_coroutine_function(self) -> None:
        for name in EXPECTED_TOOL_NAMES:
            self.assertTrue(inspect.iscoroutinefunction(getattr(self.plugin, name)), name)

    def test_sdk_component_declarations_carry_no_top_level_session_keys(self) -> None:
        """SDK 只把声明放进 metadata；顶层键必须由 `get_components()` 提升。"""

        for component in collect_components(self.plugin):
            self.assertNotIn("chat_scope", component)
            self.assertNotIn("allowed_session", component)


class PluginIdentityTests(unittest.TestCase):
    """插件类的配置模型绑定、工厂约定与插件 ID。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.plugin = plugin_module.create_plugin()

    def test_plugin_class_binds_config_model(self) -> None:
        self.assertIs(type(self.plugin).config_model, config_module.ReadHistoryConfig)

    def test_sdk_resolves_config_model(self) -> None:
        self.assertIs(type(self.plugin).get_config_model(), config_module.ReadHistoryConfig)

    def test_target_plugin_declares_a_rewritten_get_components(self) -> None:
        self.assertIsNot(
            type(self.plugin).get_components,
            plugin_module.MaiBotPlugin.get_components,
        )

    def test_create_plugin_factory_takes_no_arguments(self) -> None:
        self.assertEqual(inspect.signature(plugin_module.create_plugin).parameters, {})

    def test_create_plugin_returns_plugin_instance(self) -> None:
        self.assertIsInstance(plugin_module.create_plugin(), plugin_module.ReadHistoryPlugin)

    def test_plugin_id_constant_matches_manifest(self) -> None:
        with (PLUGIN_DIR / "_manifest.json").open("r", encoding="utf-8") as manifest_file:
            self.assertEqual(plugin_module.PLUGIN_ID, json.load(manifest_file)["id"])


if __name__ == "__main__":
    unittest.main()
