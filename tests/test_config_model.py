"""`config.py` 配置节的构造与校验顺序测试（用真实 maibot_sdk，本机 venv 可跑）。

组 1 评审指出「AccessSection 的构造与校验先后顺序缺测试」，这里补上：校验必须发生在
SDK 字段校验之前，否则 pydantic 的宽松归一（`"true"` → True、数字串 → list 元素）会
把非法输入先收下；白名单归一化也必须发生在字段赋值之前，存进配置的就是可直接用作
`allowed_session` 的形态。期望值是 tasks.md 任务 1.2 验收标准的字面量。
"""

from __future__ import annotations

import copy
import unittest

from support import load_plugin_module

config_module = load_plugin_module("config")
AccessSection = config_module.AccessSection
ReadHistoryConfig = config_module.ReadHistoryConfig


class AccessSectionValidationOrderTests(unittest.TestCase):
    """白名单与私聊开关在 pydantic 赋值前就被校验与归一化。"""

    def test_whitelist_is_normalized_before_assignment(self) -> None:
        section = AccessSection(readable_streams=[" 1919810 ", "1919810", "805457685"])
        self.assertEqual(section.readable_streams, ["1919810", "805457685"])

    def test_empty_whitelist_entry_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AccessSection(readable_streams=["1919810", ""])

    def test_non_numeric_whitelist_entry_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AccessSection(readable_streams=["abc"])

    def test_non_string_whitelist_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AccessSection(readable_streams="1919810")

    def test_string_private_switch_is_rejected_before_pydantic_coercion(self) -> None:
        """`"true"` 会被 pydantic 收成 True，必须在赋值前拒绝。"""

        with self.assertRaises(ValueError):
            AccessSection(private_enabled="true")

    def test_numeric_private_switch_is_rejected_before_pydantic_coercion(self) -> None:
        with self.assertRaises(ValueError):
            AccessSection(private_enabled=1)

    def test_valid_private_switch_is_kept(self) -> None:
        self.assertIs(AccessSection(private_enabled=True).private_enabled, True)

    def test_caller_dict_is_not_mutated(self) -> None:
        payload = {"readable_streams": [" 1919810 ", "1919810"], "private_enabled": False}
        snapshot = copy.deepcopy(payload)
        AccessSection(**payload)
        self.assertEqual(payload, snapshot)


class NestedAccessSectionTests(unittest.TestCase):
    """嵌套校验走同一条路径：`ReadHistoryConfig(access={…})` 也要归一化与拒绝。"""

    def test_nested_whitelist_is_normalized(self) -> None:
        config = ReadHistoryConfig(access={"readable_streams": [" 7 ", "7"], "private_enabled": True})
        self.assertEqual(config.access.readable_streams, ["7"])
        self.assertIs(config.access.private_enabled, True)

    def test_nested_invalid_whitelist_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ReadHistoryConfig(access={"readable_streams": ["abc"]})

    def test_default_config_has_empty_whitelist_and_closed_switch(self) -> None:
        config = ReadHistoryConfig()
        self.assertEqual(config.access.readable_streams, [])
        self.assertIs(config.access.private_enabled, False)


if __name__ == "__main__":
    unittest.main()
