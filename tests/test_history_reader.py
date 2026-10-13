"""`history_reader.py` 的纯逻辑测试（不依赖 maibot_sdk，本机可跑）。

任务 3.3 明确列为本机可测的纯逻辑：条数钳制与渲染。期望值取自 spec
`chat-history-tools`「read_recent_messages」场景与 tasks.md 验收的字面量。
"""

from __future__ import annotations

import unittest

from support import load_plugin_module, message_dict

history_reader = load_plugin_module("history_reader")


class ClampLimitTests(unittest.TestCase):
    """条数钳制：缺省取默认，超出按上限，非法退回默认。"""

    def test_absent_uses_default(self) -> None:
        self.assertEqual(history_reader.clamp_limit(None, 20, 50), 20)

    def test_within_range_is_kept(self) -> None:
        self.assertEqual(history_reader.clamp_limit(5, 20, 50), 5)

    def test_above_max_is_clamped(self) -> None:
        self.assertEqual(history_reader.clamp_limit(999, 20, 50), 50)

    def test_non_positive_falls_back_to_default(self) -> None:
        self.assertEqual(history_reader.clamp_limit(0, 20, 50), 20)
        self.assertEqual(history_reader.clamp_limit(-3, 20, 50), 20)

    def test_numeric_string_is_accepted(self) -> None:
        self.assertEqual(history_reader.clamp_limit("30", 20, 50), 30)

    def test_bool_falls_back_to_default(self) -> None:
        self.assertEqual(history_reader.clamp_limit(True, 20, 50), 20)

    def test_default_above_max_still_capped(self) -> None:
        self.assertEqual(history_reader.clamp_limit(None, 100, 50), 50)


class FormatTimestampTests(unittest.TestCase):
    """时间戳格式化：epoch 秒字符串转成可读形式，脏数据原样回显。"""

    def test_epoch_string_is_formatted(self) -> None:
        formatted = history_reader.format_timestamp("1786000000.0")
        self.assertRegex(formatted, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_invalid_value_is_passed_through(self) -> None:
        self.assertEqual(history_reader.format_timestamp("not-a-time"), "not-a-time")


class MessageRenderingTests(unittest.TestCase):
    """消息行渲染：`[时间] 昵称：内容`，备注优先。"""

    def test_line_uses_nickname_and_text(self) -> None:
        message = message_dict(timestamp="1786000000.0", text="你好", nickname="小明")
        self.assertTrue(history_reader.message_line(message).endswith("小明：你好"))

    def test_cardname_wins_over_nickname(self) -> None:
        message = message_dict(timestamp="1786000000.0", text="你好", nickname="小明", cardname="班长")
        self.assertIn("班长：你好", history_reader.message_line(message))

    def test_missing_text_gets_placeholder(self) -> None:
        message = message_dict(timestamp="1786000000.0", text="")
        self.assertIn(history_reader.NON_TEXT_PLACEHOLDER, history_reader.message_line(message))

    def test_render_history_header_carries_label_and_count(self) -> None:
        messages = [
            message_dict(timestamp="1786000001.0", text="第二", message_id="m2"),
            message_dict(timestamp="1786000000.0", text="第一", message_id="m1"),
        ]
        content = history_reader.render_history("测试群", messages)
        self.assertTrue(content.startswith("目标「测试群」的最近 2 条消息"))
        self.assertLess(content.index("第一"), content.index("第二"))

    def test_render_no_messages_names_label(self) -> None:
        self.assertEqual(
            history_reader.render_no_messages("测试群"),
            "目标「测试群」没有可查的上下文消息。",
        )


class StreamListRenderingTests(unittest.TestCase):
    """可读清单渲染：群名兜底与私聊截断说明。"""

    def test_group_name_falls_back_to_number(self) -> None:
        content = history_reader.render_stream_list(["1919810"], {}, [], 20)
        self.assertIn("- 群聊 1919810（1919810）", content)

    def test_group_name_is_used_when_present(self) -> None:
        content = history_reader.render_stream_list(["1919810"], {"1919810": "测试群"}, [], 20)
        self.assertIn("- 群聊 1919810（测试群）", content)

    def test_empty_list_message(self) -> None:
        self.assertEqual(
            history_reader.render_stream_list([], {}, [], 20),
            "当前没有可读取的聊天流。",
        )

    def test_unconfirmed_note_is_appended_with_count(self) -> None:
        content = history_reader.render_stream_list(["1919810"], {}, [], 20, 2)
        self.assertIn("白名单中有 2 个条目未被确认为群，不会生效。", content)

    def test_no_unconfirmed_note_when_count_is_zero(self) -> None:
        content = history_reader.render_stream_list(["1919810"], {}, [], 20, 0)
        self.assertNotIn("未确认", content)

    def test_empty_list_still_reports_unconfirmed_entries(self) -> None:
        content = history_reader.render_stream_list([], {}, [], 20, 1)
        self.assertIn("当前没有可读取的聊天流。", content)
        self.assertIn("白名单中有 1 个条目未被确认为群，不会生效。", content)


if __name__ == "__main__":
    unittest.main()
