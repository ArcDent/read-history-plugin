"""三个 LLM 工具的 execute 全链路测试（用真实 maibot_sdk，本机 venv 可跑）。

这是任务 3.2 / 3.3 / 3.4 的主要接缝：用桩上下文（`ctx.db` / `ctx.chat` / `ctx.message`
返回罐头会话行与消息字典）驱动真实的 `execute()`，断言工具返回的 content / error 文本。
期望值取自 spec `chat-history-tools` 场景的字面量。
"""

from __future__ import annotations

import logging
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from support import (
    GROUP,
    OTHER_GROUP,
    PRIVATE_PEER,
    SESSION_OF_GROUP,
    SESSION_OF_PEER,
    StubContext,
    StubDatabase,
    StubMessage,
    group_row,
    load_plugin_entry,
    load_plugin_module,
    make_plugin,
    message_dict,
    plugin_config,
    private_row,
)

plugin_module = load_plugin_entry()
stream_resolver = load_plugin_module("stream_resolver")


class ListReadableStreamsTests(unittest.IsolatedAsyncioTestCase):
    """spec「list_readable_streams 返回当前可读的目标清单」的三个场景。"""

    async def test_whitelisted_groups_are_listed_with_names(self) -> None:
        ctx = StubContext(db=StubDatabase([group_row()]))
        plugin = make_plugin(plugin_config([GROUP]), ctx)
        result = await plugin.list_readable_streams()
        self.assertIs(result["success"], True)
        content = result["content"]
        self.assertIn("当前可读取的聊天流", content)
        self.assertIn("- 群聊 1919810（测试群）", content)

    async def test_group_name_falls_back_to_number(self) -> None:
        ctx = StubContext(db=StubDatabase([group_row(group_name="")]))
        plugin = make_plugin(plugin_config([GROUP]), ctx)
        content = (await plugin.list_readable_streams())["content"]
        self.assertIn("- 群聊 1919810（1919810）", content)

    async def test_unconfirmed_entry_is_not_listed_and_gets_a_note(self) -> None:
        """spec 场景「未确认的条目不作为可读目标出现」：不列该条目，另加一行提示。"""

        ctx = StubContext(db=StubDatabase([group_row()]))
        plugin = make_plugin(plugin_config([GROUP, PRIVATE_PEER]), ctx)
        content = (await plugin.list_readable_streams())["content"]
        self.assertIn("- 群聊 1919810（测试群）", content)
        self.assertNotIn(PRIVATE_PEER, content)
        self.assertIn("白名单中有 1 个条目未被确认为群，不会生效。", content)

    async def test_all_confirmed_groups_have_no_unconfirmed_note(self) -> None:
        ctx = StubContext(db=StubDatabase([group_row()]))
        plugin = make_plugin(plugin_config([GROUP]), ctx)
        content = (await plugin.list_readable_streams())["content"]
        self.assertNotIn("未确认", content)

    async def test_snapshot_confirmed_entry_is_listed_without_live_session(self) -> None:
        """快照里确认过的条目即使本次查不到群会话也照常列出，不算未确认。"""

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            stream_resolver.write_known_group_ids(data_dir, [GROUP])
            plugin = make_plugin(
                plugin_config([GROUP]), StubContext(db=StubDatabase([]), data_dir=data_dir)
            )
            content = (await plugin.list_readable_streams())["content"]
        self.assertIn("- 群聊 1919810（1919810）", content)
        self.assertNotIn("未确认", content)

    async def test_only_unconfirmed_entries_yields_empty_list_with_note(self) -> None:
        plugin = make_plugin(plugin_config([PRIVATE_PEER]), StubContext(db=StubDatabase([])))
        content = (await plugin.list_readable_streams())["content"]
        self.assertIn("当前没有可读取的聊天流。", content)
        self.assertNotIn("- 群聊", content)
        self.assertIn("白名单中有 1 个条目未被确认为群，不会生效。", content)

    async def test_private_switch_on_lists_private_targets(self) -> None:
        ctx = StubContext(db=StubDatabase([group_row(), private_row()]))
        plugin = make_plugin(plugin_config([GROUP], private_enabled=True), ctx)
        content = (await plugin.list_readable_streams())["content"]
        self.assertIn("- 群聊 1919810（测试群）", content)
        self.assertIn("- 私聊 10001（小明）", content)

    async def test_private_switch_off_hides_private_targets(self) -> None:
        ctx = StubContext(db=StubDatabase([group_row(), private_row()]))
        plugin = make_plugin(plugin_config([GROUP], private_enabled=False), ctx)
        content = (await plugin.list_readable_streams())["content"]
        self.assertNotIn("私聊", content)

    async def test_empty_whitelist_and_private_off_yields_empty_list(self) -> None:
        plugin = make_plugin(plugin_config([]), StubContext())
        result = await plugin.list_readable_streams()
        self.assertIs(result["success"], True)
        self.assertEqual(result["content"], "当前没有可读取的聊天流。")

    async def test_private_targets_are_truncated_with_a_note(self) -> None:
        limit = plugin_module.PRIVATE_TARGET_LIMIT
        rows = [group_row()]
        for index in range(limit + 5):
            rows.append(
                private_row(
                    user_id=str(20000 + index),
                    nickname=f"用户{index}",
                    session_id=f"{index:032x}",
                    last_active=f"2026-10-{index + 1:02d}T10:00:00",
                )
            )
        plugin = make_plugin(plugin_config([GROUP], private_enabled=True), StubContext(db=StubDatabase(rows)))
        content = (await plugin.list_readable_streams())["content"]
        listed = [line for line in content.splitlines() if line.startswith("- 私聊")]
        self.assertEqual(len(listed), limit)
        self.assertIn("仅列出最近活跃的前", content)
        # 最近活跃的（index 越大越新）必须留下，最旧的被截掉。
        self.assertIn("用户24", content)
        self.assertNotIn("用户0（", content)


class ReadRecentMessagesTests(unittest.IsolatedAsyncioTestCase):
    """spec「read_recent_messages 读取指定目标最近的 n 条原始消息」的场景。"""

    def _group_ctx(self, messages: list[dict[str, Any]], **kwargs: Any) -> StubContext:
        return StubContext(
            db=StubDatabase([group_row()]),
            message=StubMessage({SESSION_OF_GROUP: messages}, **kwargs),
        )

    def _message_lines(self, content: str) -> list[str]:
        return [line for line in content.splitlines() if line.startswith("[")]

    async def test_default_limit_is_used_when_limit_absent(self) -> None:
        messages = [
            message_dict(timestamp=str(1786000000 + index), text=f"消息{index}", message_id=f"m{index}")
            for index in range(30)
        ]
        plugin = make_plugin(plugin_config([GROUP], default_limit=20), self._group_ctx(messages))
        result = await plugin.read_recent_messages(target_group_id=GROUP)
        self.assertIs(result["success"], True)
        lines = self._message_lines(result["content"])
        self.assertEqual(len(lines), 20)
        self.assertIn("消息29", result["content"])
        self.assertNotIn("消息0：", result["content"])

    async def test_huge_limit_is_clamped_to_max(self) -> None:
        messages = [
            message_dict(timestamp=str(1786000000 + index), text=f"消息{index}", message_id=f"m{index}")
            for index in range(30)
        ]
        plugin = make_plugin(plugin_config([GROUP], default_limit=20, max_limit=5), self._group_ctx(messages))
        result = await plugin.read_recent_messages(target_group_id=GROUP, limit=999)
        self.assertIs(result["success"], True)
        self.assertEqual(len(self._message_lines(result["content"])), 5)

    async def test_command_messages_are_excluded(self) -> None:
        messages = [
            message_dict(timestamp="1786000000.0", text="正常消息", message_id="m1", is_command=False),
            message_dict(timestamp="1786000001.0", text="/订阅 天气", message_id="m2", is_command=True),
        ]
        plugin = make_plugin(plugin_config([GROUP]), self._group_ctx(messages))
        content = (await plugin.read_recent_messages(target_group_id=GROUP))["content"]
        self.assertIn("正常消息", content)
        self.assertNotIn("/订阅", content)

    async def test_bot_messages_are_hidden_by_default(self) -> None:
        bot_id = "99999"
        messages = [
            message_dict(timestamp="1786000000.0", text="机器人说话", user_id=bot_id, message_id="m1"),
            message_dict(timestamp="1786000001.0", text="群友说话", user_id="10001", message_id="m2"),
        ]
        plugin = make_plugin(plugin_config([GROUP]), self._group_ctx(messages, bot_ids={bot_id}))
        content = (await plugin.read_recent_messages(target_group_id=GROUP))["content"]
        self.assertNotIn("机器人说话", content)
        self.assertIn("群友说话", content)

    async def test_bot_messages_are_shown_when_enabled(self) -> None:
        bot_id = "99999"
        messages = [
            message_dict(timestamp="1786000000.0", text="机器人说话", user_id=bot_id, message_id="m1"),
        ]
        plugin = make_plugin(
            plugin_config([GROUP], include_bot_messages=True),
            self._group_ctx(messages, bot_ids={bot_id}),
        )
        content = (await plugin.read_recent_messages(target_group_id=GROUP))["content"]
        self.assertIn("机器人说话", content)

    async def test_messages_are_ordered_chronologically(self) -> None:
        messages = [
            message_dict(timestamp="1786000002.0", text="第三", message_id="m3"),
            message_dict(timestamp="1786000000.0", text="第一", message_id="m1"),
            message_dict(timestamp="1786000001.0", text="第二", message_id="m2"),
        ]
        plugin = make_plugin(plugin_config([GROUP]), self._group_ctx(messages))
        content = (await plugin.read_recent_messages(target_group_id=GROUP))["content"]
        self.assertLess(content.index("第一"), content.index("第二"))
        self.assertLess(content.index("第二"), content.index("第三"))

    async def test_header_carries_target_label(self) -> None:
        messages = [message_dict(timestamp="1786000000.0", text="你好", message_id="m1")]
        plugin = make_plugin(plugin_config([GROUP]), self._group_ctx(messages))
        content = (await plugin.read_recent_messages(target_group_id=GROUP))["content"]
        self.assertIn("目标「测试群」", content)

    async def test_unreadable_target_is_rejected(self) -> None:
        plugin = make_plugin(plugin_config([GROUP]), self._group_ctx([]))
        result = await plugin.read_recent_messages(target_group_id=OTHER_GROUP)
        self.assertIs(result["success"], False)
        self.assertIn("不可读", result["error"])
        self.assertIn(OTHER_GROUP, result["error"])

    async def test_target_without_messages_is_reported(self) -> None:
        plugin = make_plugin(plugin_config([GROUP]), self._group_ctx([]))
        result = await plugin.read_recent_messages(target_group_id=GROUP)
        self.assertIs(result["success"], True)
        self.assertEqual(result["content"], "目标「测试群」没有可查的上下文消息。")

    async def test_wide_time_window_keeps_messages_older_than_24h(self) -> None:
        old_timestamp = time.time() - 25 * 3600
        messages = [message_dict(timestamp=str(old_timestamp), text="昨天的消息", message_id="m1")]
        ctx = self._group_ctx(messages)
        plugin = make_plugin(plugin_config([GROUP]), ctx)
        content = (await plugin.read_recent_messages(target_group_id=GROUP))["content"]
        self.assertIn("昨天的消息", content)
        call = ctx.message.calls[0]
        self.assertEqual(call["start_time"], 0)
        self.assertGreaterEqual(call["end_time"], old_timestamp)

    async def test_private_target_is_readable_when_switch_on(self) -> None:
        ctx = StubContext(
            db=StubDatabase([private_row()]),
            message=StubMessage({SESSION_OF_PEER: [message_dict(timestamp="1786000000.0", text="私聊消息")]}),
        )
        plugin = make_plugin(plugin_config([GROUP], private_enabled=True), ctx)
        result = await plugin.read_recent_messages(target_user_id=PRIVATE_PEER)
        self.assertIs(result["success"], True)
        self.assertIn("私聊消息", result["content"])

    async def test_private_target_is_rejected_when_switch_off(self) -> None:
        plugin = make_plugin(plugin_config([GROUP], private_enabled=False), StubContext(db=StubDatabase([private_row()])))
        result = await plugin.read_recent_messages(target_user_id=PRIVATE_PEER)
        self.assertIs(result["success"], False)
        self.assertIn("不可读", result["error"])


class SharedFailureWrapperTests(unittest.IsolatedAsyncioTestCase):
    """评审意见：三个工具共用统一失败包装，未预期异常不外抛。"""

    async def _boom(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    async def test_reading_tools_wrap_unexpected_errors(self) -> None:
        plugin = make_plugin(plugin_config([GROUP]), StubContext(db=StubDatabase([group_row()])))
        with (
            self.assertLogs(plugin_module._LOGGER, level=logging.WARNING),
            mock.patch.object(plugin, "_resolve_readable_target", self._boom),
        ):
            for call in (plugin.read_chat_summary, plugin.read_recent_messages):
                result = await call(target_group_id=GROUP)
                self.assertIs(result["success"], False)
                self.assertIn("时出错", result["error"])

    async def test_list_tool_wraps_unexpected_errors(self) -> None:
        plugin = make_plugin(plugin_config([GROUP]), StubContext(db=StubDatabase([group_row()])))
        with (
            self.assertLogs(plugin_module._LOGGER, level=logging.WARNING),
            mock.patch.object(plugin_module, "fetch_group_sessions", self._boom),
        ):
            result = await plugin.list_readable_streams()
        self.assertIs(result["success"], False)
        self.assertIn("时出错", result["error"])


class TargetArgumentErrorTests(unittest.IsolatedAsyncioTestCase):
    """spec「目标参数与错误反馈可被 LLM 自我纠正」的四类情况。"""

    def _plugin(self, readable_streams: list[str] | None = None, db_rows: list[dict[str, Any]] | None = None) -> Any:
        return make_plugin(
            plugin_config(readable_streams if readable_streams is not None else [GROUP]),
            StubContext(db=StubDatabase(db_rows if db_rows is not None else [group_row()])),
        )

    def _import_target_params(self) -> Any:
        from support import load_plugin_module

        return load_plugin_module("target_params")

    async def test_both_missing_returns_dedicated_error(self) -> None:
        result = await self._plugin().read_recent_messages()
        self.assertIs(result["success"], False)
        self.assertEqual(result["error"], self._import_target_params().MISSING_BOTH_ERROR)

    async def test_both_present_returns_dedicated_error(self) -> None:
        result = await self._plugin().read_recent_messages(
            target_group_id=GROUP, target_user_id=PRIVATE_PEER
        )
        self.assertIs(result["success"], False)
        self.assertEqual(result["error"], self._import_target_params().BOTH_PRESENT_ERROR)

    async def test_invalid_format_returns_dedicated_error(self) -> None:
        result = await self._plugin().read_recent_messages(target_group_id="abc")
        self.assertIs(result["success"], False)
        self.assertEqual(result["error"], self._import_target_params().INVALID_FORMAT_ERROR)

    async def test_not_found_returns_dedicated_error(self) -> None:
        result = await self._plugin(db_rows=[]).read_recent_messages(target_group_id=GROUP)
        self.assertIs(result["success"], False)
        self.assertIn("未找到", result["error"])
        self.assertIn(GROUP, result["error"])

    async def test_error_branches_never_raise(self) -> None:
        plugin = self._plugin()
        for arguments in (
            {},
            {"target_group_id": GROUP, "target_user_id": PRIVATE_PEER},
            {"target_group_id": ""},
            {"target_group_id": 12345},
            {"target_group_id": "abc"},
            {"target_user_id": "10001"},
        ):
            result = await plugin.read_recent_messages(**arguments)
            self.assertIs(result["success"], False, arguments)


if __name__ == "__main__":
    unittest.main()
