"""`target_params.py` 的纯函数测试（不依赖 maibot_sdk，本机可跑）。

任务 3.4 的参数解析接缝：两个目标参数必须且只能提供一个。缺省、同在、格式非法三类各
返回独立文案，均说明用法并建议先调 list_readable_streams；「目标不存在」由解析层判定后
取文案。期望值取自 spec `chat-history-tools`「目标参数与错误反馈」的场景字面量。
"""

from __future__ import annotations

import unittest

from support import load_plugin_module

target_params = load_plugin_module("target_params")

GROUP = "1919810"
PRIVATE_PEER = "10001"


class ParseTargetTests(unittest.TestCase):
    """`parse_target`：二选一的判定与归一化。"""

    def test_valid_group_target(self) -> None:
        target, error = target_params.parse_target({"target_group_id": GROUP})
        self.assertEqual((target.target_type, target.target_id), (target_params.TARGET_TYPE_GROUP, GROUP))
        self.assertEqual(error, "")

    def test_valid_user_target(self) -> None:
        target, error = target_params.parse_target({"target_user_id": PRIVATE_PEER})
        self.assertEqual(
            (target.target_type, target.target_id), (target_params.TARGET_TYPE_PRIVATE, PRIVATE_PEER)
        )
        self.assertEqual(error, "")

    def test_value_is_stripped(self) -> None:
        target, _error = target_params.parse_target({"target_group_id": f"  {GROUP}  "})
        self.assertEqual(target.target_id, GROUP)

    def test_integer_value_is_accepted(self) -> None:
        target, error = target_params.parse_target({"target_group_id": 1919810})
        self.assertEqual((target.target_type, target.target_id), (target_params.TARGET_TYPE_GROUP, GROUP))
        self.assertEqual(error, "")

    def test_both_missing_error(self) -> None:
        for arguments in ({}, {"target_group_id": None}, {"other": "x"}):
            target, error = target_params.parse_target(arguments)
            self.assertIsNone(target, arguments)
            self.assertEqual(error, target_params.MISSING_BOTH_ERROR, arguments)

    def test_both_present_error(self) -> None:
        target, error = target_params.parse_target(
            {"target_group_id": GROUP, "target_user_id": PRIVATE_PEER}
        )
        self.assertIsNone(target)
        self.assertEqual(error, target_params.BOTH_PRESENT_ERROR)

    def test_non_numeric_value_error(self) -> None:
        for bad in ("abc", "19a", "１９１９８１０", "  "):
            _target, error = target_params.parse_target({"target_group_id": bad})
            self.assertEqual(error, target_params.INVALID_FORMAT_ERROR, bad)

    def test_empty_string_is_invalid_not_missing(self) -> None:
        _target, error = target_params.parse_target({"target_group_id": ""})
        self.assertEqual(error, target_params.INVALID_FORMAT_ERROR)

    def test_bool_value_is_invalid(self) -> None:
        _target, error = target_params.parse_target({"target_group_id": True})
        self.assertEqual(error, target_params.INVALID_FORMAT_ERROR)

    def test_non_mapping_arguments_are_missing(self) -> None:
        _target, error = target_params.parse_target(None)  # type: ignore[arg-type]
        self.assertEqual(error, target_params.MISSING_BOTH_ERROR)


class ErrorMessageTests(unittest.TestCase):
    """四类文案互不歧义，且都建议先调 list_readable_streams。"""

    def test_four_categories_are_pairwise_distinct(self) -> None:
        messages = {
            target_params.MISSING_BOTH_ERROR,
            target_params.BOTH_PRESENT_ERROR,
            target_params.INVALID_FORMAT_ERROR,
            target_params.not_found_error(
                target_params.Target(target_params.TARGET_TYPE_GROUP, GROUP)
            ),
        }
        self.assertEqual(len(messages), 4)

    def test_every_message_suggests_the_list_tool(self) -> None:
        messages = [
            target_params.MISSING_BOTH_ERROR,
            target_params.BOTH_PRESENT_ERROR,
            target_params.INVALID_FORMAT_ERROR,
            target_params.not_found_error(
                target_params.Target(target_params.TARGET_TYPE_GROUP, GROUP)
            ),
            target_params.not_found_error(
                target_params.Target(target_params.TARGET_TYPE_PRIVATE, PRIVATE_PEER)
            ),
            target_params.unreadable_group_error(GROUP),
            target_params.unreadable_private_error(),
        ]
        for message in messages:
            self.assertIn("list_readable_streams", message, message)

    def test_not_found_message_names_the_target_kind(self) -> None:
        self.assertIn(
            "群号",
            target_params.not_found_error(
                target_params.Target(target_params.TARGET_TYPE_GROUP, GROUP)
            ),
        )
        self.assertIn(
            "QQ 号",
            target_params.not_found_error(
                target_params.Target(target_params.TARGET_TYPE_PRIVATE, PRIVATE_PEER)
            ),
        )


if __name__ == "__main__":
    unittest.main()
