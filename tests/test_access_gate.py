"""`access_gate.py` 的行为测试（不依赖 maibot_sdk，本机可跑）。

断言里的期望值全部是 spec `chat-access-control` 场景的字面量：

- 白名单内的群既暴露工具又可读，白名单外的群两者皆假。
- 私聊不暴露工具；作为读取目标只由私聊开关决定，与白名单内容无关，白名单里写 QQ 号
  不产生作用（ADR-0003）。
- access 节（白名单与私聊开关）签名变化触发一次自重载，非 access 变化不触发，
  重载失败只记日志。
"""

from __future__ import annotations

import logging
import unittest

from support import load_plugin_module

access_gate = load_plugin_module("access_gate")


class GroupReadabilityTests(unittest.TestCase):
    """spec「白名单决定群聊的工具暴露与可读范围」的可读侧。"""

    def test_group_in_whitelist_is_readable(self) -> None:
        self.assertIs(access_gate.group_is_readable(["1919810"], "1919810"), True)

    def test_group_outside_whitelist_is_not_readable(self) -> None:
        self.assertIs(access_gate.group_is_readable(["1919810"], "805457685"), False)

    def test_group_with_empty_whitelist_is_not_readable(self) -> None:
        self.assertIs(access_gate.group_is_readable([], "1919810"), False)

    def test_group_with_whitespace_padded_entry_is_readable(self) -> None:
        self.assertIs(access_gate.group_is_readable([" 1919810 "], "1919810"), True)

    def test_group_with_invalid_entry_is_not_readable(self) -> None:
        """条目非法时整份白名单按空处理，不能因为写了非法文本就放行。"""

        self.assertIs(access_gate.group_is_readable(["abc"], "abc"), False)

    def test_group_readability_ignores_target_whitespace(self) -> None:
        self.assertIs(access_gate.group_is_readable(["1919810"], " 1919810 "), True)


class PrivateReadabilityTests(unittest.TestCase):
    """spec「私聊只作为读取目标」的可读侧：判定只有一个条件，即私聊开关。"""

    def test_private_target_is_readable_when_switch_on(self) -> None:
        self.assertIs(access_gate.private_is_readable(True), True)

    def test_private_target_is_not_readable_when_switch_off(self) -> None:
        self.assertIs(access_gate.private_is_readable(False), False)

    def test_whitelist_holding_only_group_numbers_leaves_private_readable(self) -> None:
        """① 白名单里没有这个 QQ 号（只有群号），开关打开时私聊目标依旧可读。"""

        config = {"access": {"readable_streams": ["1919810"], "private_enabled": True}}
        self.assertIs(access_gate.private_is_readable(access_gate.private_enabled(config)), True)

    def test_qq_entry_in_whitelist_does_not_change_private_readability(self) -> None:
        """② 白名单里写 QQ 号不产生作用：可读性与名单里有没有这个号无关。"""

        without_entry = {"access": {"readable_streams": [], "private_enabled": True}}
        with_entry = {"access": {"readable_streams": ["10001"], "private_enabled": True}}
        self.assertEqual(
            access_gate.private_is_readable(access_gate.private_enabled(without_entry)),
            access_gate.private_is_readable(access_gate.private_enabled(with_entry)),
        )
        self.assertIs(access_gate.private_is_readable(access_gate.private_enabled(with_entry)), True)

    def test_private_readability_ignores_whitelist_shape(self) -> None:
        """白名单缺失或非法都不参与私聊判定：开关打开就是可读。"""

        for config in (
            {"access": {"private_enabled": True}},
            {"access": {"readable_streams": ["abc"], "private_enabled": True}},
        ):
            self.assertIs(
                access_gate.private_is_readable(access_gate.private_enabled(config)), True, config
            )

    def test_whitelisted_number_is_readable_as_group_but_not_forced_on_private(self) -> None:
        """同一个数字串：群可读性看名单，私聊可读性看开关，两者互不牵连。"""

        entries = ["1919810"]
        self.assertIs(access_gate.group_is_readable(entries, "1919810"), True)
        config = {"access": {"readable_streams": entries, "private_enabled": False}}
        self.assertIs(access_gate.private_is_readable(access_gate.private_enabled(config)), False)


class AccessSignatureTests(unittest.TestCase):
    """access 节的可比签名：只看白名单与私聊开关。"""

    def test_signature_covers_whitelist_and_private_switch(self) -> None:
        self.assertEqual(
            access_gate.access_signature(
                {"access": {"readable_streams": [" 1919810 ", "1919810"], "private_enabled": False}}
            ),
            (("1919810",), False),
        )

    def test_signature_changes_with_whitelist(self) -> None:
        before = access_gate.access_signature({"access": {"readable_streams": ["1919810"]}})
        after = access_gate.access_signature(
            {"access": {"readable_streams": ["1919810", "805457685"]}}
        )
        self.assertNotEqual(before, after)

    def test_signature_changes_with_private_switch(self) -> None:
        before = access_gate.access_signature(
            {"access": {"readable_streams": ["10001"], "private_enabled": False}}
        )
        after = access_gate.access_signature(
            {"access": {"readable_streams": ["10001"], "private_enabled": True}}
        )
        self.assertNotEqual(before, after)

    def test_signature_ignores_non_access_sections(self) -> None:
        before = access_gate.access_signature(
            {"access": {"readable_streams": ["1919810"]}, "recent": {"default_limit": 20}}
        )
        after = access_gate.access_signature(
            {"access": {"readable_streams": ["1919810"]}, "recent": {"default_limit": 40}}
        )
        self.assertEqual(before, after)

    def test_signature_of_missing_access_section_is_empty(self) -> None:
        self.assertEqual(access_gate.access_signature({}), ((), False))

    def test_signature_of_invalid_whitelist_is_empty(self) -> None:
        self.assertEqual(
            access_gate.access_signature({"access": {"readable_streams": ["abc"]}}),
            ((), False),
        )


class RecordingReloader:
    """记录调用次数的自重载桩。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._error = error

    async def __call__(self) -> None:
        self.calls.append("reload")
        if self._error is not None:
            raise self._error


class ReloadOnAccessChangeTests(unittest.IsolatedAsyncioTestCase):
    """配置热更新的门禁处理：变则调一次、不变不调、异常只记日志。"""

    def setUp(self) -> None:
        self.logger = logging.getLogger("read_history_plugin.tests.access_gate")
        self.before = access_gate.access_signature(
            {"access": {"readable_streams": ["1919810"], "private_enabled": False}}
        )

    async def test_changed_access_triggers_exactly_one_reload(self) -> None:
        reloader = RecordingReloader()
        signature = await access_gate.reload_on_access_change(
            scope="self",
            config_data={"access": {"readable_streams": ["1919810", "805457685"], "private_enabled": False}},
            previous_signature=self.before,
            reload_plugin=reloader,
            logger=self.logger,
        )
        self.assertEqual(len(reloader.calls), 1)
        self.assertEqual(signature, (("1919810", "805457685"), False))

    async def test_private_switch_change_triggers_one_reload(self) -> None:
        reloader = RecordingReloader()
        signature = await access_gate.reload_on_access_change(
            scope="self",
            config_data={"access": {"readable_streams": ["1919810"], "private_enabled": True}},
            previous_signature=self.before,
            reload_plugin=reloader,
            logger=self.logger,
        )
        self.assertEqual(len(reloader.calls), 1)
        self.assertEqual(signature, (("1919810",), True))

    async def test_unchanged_access_does_not_reload(self) -> None:
        reloader = RecordingReloader()
        signature = await access_gate.reload_on_access_change(
            scope="self",
            config_data={"access": {"readable_streams": ["1919810"], "private_enabled": False}},
            previous_signature=self.before,
            reload_plugin=reloader,
            logger=self.logger,
        )
        self.assertEqual(reloader.calls, [])
        self.assertEqual(signature, self.before)

    async def test_non_access_change_does_not_reload(self) -> None:
        reloader = RecordingReloader()
        await access_gate.reload_on_access_change(
            scope="self",
            config_data={
                "access": {"readable_streams": ["1919810"], "private_enabled": False},
                "recent": {"default_limit": 40, "max_limit": 50, "include_bot_messages": True},
            },
            previous_signature=self.before,
            reload_plugin=reloader,
            logger=self.logger,
        )
        self.assertEqual(reloader.calls, [])

    async def test_first_update_only_records_baseline(self) -> None:
        reloader = RecordingReloader()
        signature = await access_gate.reload_on_access_change(
            scope="self",
            config_data={"access": {"readable_streams": ["805457685"]}},
            previous_signature=None,
            reload_plugin=reloader,
            logger=self.logger,
        )
        self.assertEqual(reloader.calls, [])
        self.assertEqual(signature, (("805457685",), False))

    async def test_global_scope_is_ignored(self) -> None:
        reloader = RecordingReloader()
        signature = await access_gate.reload_on_access_change(
            scope="bot",
            config_data={"access": {"readable_streams": ["805457685"]}},
            previous_signature=self.before,
            reload_plugin=reloader,
            logger=self.logger,
        )
        self.assertEqual(reloader.calls, [])
        self.assertEqual(signature, self.before)

    async def test_reload_failure_is_logged_and_not_raised(self) -> None:
        reloader = RecordingReloader(error=RuntimeError("宿主拒绝重载"))
        with self.assertLogs(self.logger, level=logging.WARNING) as captured:
            signature = await access_gate.reload_on_access_change(
                scope="self",
                config_data={"access": {"readable_streams": ["1919810", "805457685"], "private_enabled": False}},
                previous_signature=self.before,
                reload_plugin=reloader,
                logger=self.logger,
            )
        self.assertEqual(len(reloader.calls), 1)
        self.assertEqual(signature, (("1919810", "805457685"), False))
        self.assertTrue(any("重载" in line for line in captured.output), captured.output)


class ReadableEntriesTests(unittest.TestCase):
    """`readable_entries`：从配置字典取白名单，形态不对一律按空清单。"""

    def test_entries_are_normalized(self) -> None:
        self.assertEqual(
            access_gate.readable_entries({"access": {"readable_streams": [" 1919810 ", "1919810"]}}),
            ["1919810"],
        )

    def test_missing_access_section_is_empty(self) -> None:
        self.assertEqual(access_gate.readable_entries({}), [])

    def test_invalid_entries_are_empty(self) -> None:
        self.assertEqual(access_gate.readable_entries({"access": {"readable_streams": ["abc"]}}), [])

    def test_non_list_entries_are_empty(self) -> None:
        self.assertEqual(access_gate.readable_entries({"access": {"readable_streams": "1919810"}}), [])


if __name__ == "__main__":
    unittest.main()
