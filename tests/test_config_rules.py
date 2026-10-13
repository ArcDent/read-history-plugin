"""`config_rules.py` 纯函数的行为测试（不依赖 maibot_sdk，本机可跑）。

判定的单一定义处就是 `normalize_stream_entries`：它同时给出「归一化清单」与「拒绝
原因」，配置期（`config.py` 拒绝整份非法白名单）与门禁投影（`stream_resolver.py`
只采信合法条目）用同一份结果。期望值来自 tasks.md 任务 1.2 与 2.1 验收标准的字面量：
去空白、空串、非数字串三类输入的行为，群号与 QQ 号一律按 ASCII 十进制数字串判定。
"""

from __future__ import annotations

import unittest

from support import load_plugin_module

config_rules = load_plugin_module("config_rules")


class NumericStreamIdTests(unittest.TestCase):
    """`is_numeric_stream_id`：非空 ASCII 十进制数字串的正判与反判。"""

    def test_plain_digits_are_numeric(self) -> None:
        self.assertTrue(config_rules.is_numeric_stream_id("1919810"))

    def test_empty_text_is_not_numeric(self) -> None:
        self.assertFalse(config_rules.is_numeric_stream_id(""))

    def test_whitespace_padded_digits_are_not_numeric(self) -> None:
        self.assertFalse(config_rules.is_numeric_stream_id(" 1919810 "))

    def test_letters_are_not_numeric(self) -> None:
        self.assertFalse(config_rules.is_numeric_stream_id("abc"))

    def test_digits_mixed_with_letters_are_not_numeric(self) -> None:
        self.assertFalse(config_rules.is_numeric_stream_id("19a"))

    def test_fullwidth_digits_are_not_numeric(self) -> None:
        self.assertFalse(config_rules.is_numeric_stream_id("１９１９８１０"))


class NormalizeStreamEntriesTests(unittest.TestCase):
    """`normalize_stream_entries`：归一化与合法性判定的单一定义。"""

    def test_valid_entries_are_stripped_and_deduplicated(self) -> None:
        self.assertEqual(
            config_rules.normalize_stream_entries([" 1919810 ", "1919810", "\t805457685\n"]),
            (["1919810", "805457685"], ""),
        )

    def test_empty_entries_are_rejected_instead_of_dropped(self) -> None:
        normalized, reason = config_rules.normalize_stream_entries(["", "42"])
        self.assertEqual(normalized, [])
        self.assertNotEqual(reason, "")

    def test_whitespace_only_entry_is_rejected(self) -> None:
        normalized, reason = config_rules.normalize_stream_entries(["   "])
        self.assertEqual(normalized, [])
        self.assertNotEqual(reason, "")

    def test_non_numeric_entry_is_rejected(self) -> None:
        """合法性的定义单点：非数字串即非法，不再有一处接受、一处丢弃的分歧。"""

        normalized, reason = config_rules.normalize_stream_entries(["1919810", "abc"])
        self.assertEqual(normalized, [])
        self.assertNotEqual(reason, "")

    def test_numeric_looking_text_with_suffix_is_rejected(self) -> None:
        normalized, reason = config_rules.normalize_stream_entries(["1919810a"])
        self.assertEqual(normalized, [])
        self.assertNotEqual(reason, "")

    def test_non_string_entry_is_rejected(self) -> None:
        for bad_value in (1919810, None, ["1919810"]):
            normalized, reason = config_rules.normalize_stream_entries([bad_value])
            self.assertEqual(normalized, [], bad_value)
            self.assertNotEqual(reason, "", bad_value)

    def test_fullwidth_digits_are_rejected(self) -> None:
        normalized, reason = config_rules.normalize_stream_entries(["１９１９８１０"])
        self.assertEqual(normalized, [])
        self.assertNotEqual(reason, "")

    def test_rejection_reason_points_at_the_position(self) -> None:
        _normalized, reason = config_rules.normalize_stream_entries(["1919810", "abc"])
        self.assertIn("2", reason)

    def test_empty_input_is_valid_and_empty(self) -> None:
        self.assertEqual(config_rules.normalize_stream_entries([]), ([], ""))


if __name__ == "__main__":
    unittest.main()
