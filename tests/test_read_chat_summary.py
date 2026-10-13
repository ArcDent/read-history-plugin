"""任务 4.2：`read_chat_summary` 主流程的全链路测试（用真实 maibot_sdk，本机可跑）。

接缝是工具返回的 content / error 文本，用桩上下文（含桩 `ctx.llm.generate`）驱动真实的
`execute()`。期望值取自 summary-generation spec「按需生成」「生成失败不影响其他功能」
与 chat-history-tools spec「read_chat_summary」的场景字面量。

覆盖：新鲜摘要直接返回且不调模型；过期摘要重新生成并覆盖；首次读取触发生成并落盘；
不可读目标不生成、不存储、不返回；生成失败告知且旧摘要原样保留；输入条数受
`max_input_messages` 上限；增量起点为上次 cutoff。部署后宿主侧证据（llm_usage 无新增
记录、真实模型路由）在任务 5.2/5.3。
"""

from __future__ import annotations

import logging
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from support import (
    GROUP,
    GROUP_NAME,
    OTHER_GROUP,
    PRIVATE_PEER,
    SESSION_OF_GROUP,
    StubContext,
    StubDatabase,
    StubLLM,
    StubMessage,
    group_row,
    load_plugin_module,
    make_plugin,
    message_dict,
    plugin_config,
)

summary_store = load_plugin_module("summary_store")
summary_generator = load_plugin_module("summary_generator")
history_reader = load_plugin_module("history_reader")

TARGET_KEY = f"group:{GROUP}"
WINDOW_HOURS = 6
CANNED_SUMMARY = "这是概括文本"


def messages_around(now: float, count: int = 3) -> list[dict[str, Any]]:
    """构造 `now` 前后若干条带正文的罐头消息。"""

    return [
        message_dict(
            timestamp=str(now - index),
            text=f"消息{index}",
            message_id=f"m{index}",
            nickname="小明",
        )
        for index in range(count)
    ]


class SummaryChainTestCase(unittest.IsolatedAsyncioTestCase):
    """公共夹具：临时 data_dir、群会话与罐头消息。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        self.db_path = summary_store.summary_db_path(self.data_dir)

    def _ctx(self, messages: list[dict[str, Any]] | None = None, llm: StubLLM | None = None) -> StubContext:
        return StubContext(
            db=StubDatabase([group_row()]),
            message=StubMessage({SESSION_OF_GROUP: messages or []}),
            llm=llm if llm is not None else StubLLM(),
            data_dir=self.data_dir,
        )

    def _store(self, *, text: str, cutoff_ts: float, created_ts: float) -> None:
        summary_store.save_summary(
            self.db_path,
            target_key=TARGET_KEY,
            target_type="group",
            text=text,
            cutoff_ts=cutoff_ts,
            created_ts=created_ts,
        )


class FreshSummaryTests(SummaryChainTestCase):
    """spec 场景「新鲜摘要直接返回」：窗口内直接返回且不调模型。"""

    async def test_fresh_summary_is_returned_without_calling_the_model(self) -> None:
        now = time.time()
        self._store(text="旧而新鲜的摘要", cutoff_ts=now - 100, created_ts=now - 100)
        ctx = self._ctx(llm=StubLLM())
        content = (await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(
            target_group_id=GROUP
        ))["content"]
        self.assertIn("旧而新鲜的摘要", content)
        self.assertIn(GROUP_NAME, content)
        self.assertEqual(ctx.llm.calls, [])
        self.assertEqual(ctx.message.calls, [])

    async def test_fresh_summary_carries_the_covered_time_range(self) -> None:
        now = time.time()
        self._store(text="摘要正文", cutoff_ts=now - 100, created_ts=now - 100)
        result = await make_plugin(plugin_config([GROUP]), self._ctx()).read_chat_summary(
            target_group_id=GROUP
        )
        self.assertIs(result["success"], True)
        self.assertIn(history_reader.format_timestamp(now - 100), result["content"])


class FirstReadTests(SummaryChainTestCase):
    """spec 场景「首次读取触发生成」：无摘要则当场概括、落盘并返回。"""

    async def test_first_read_generates_stores_and_returns(self) -> None:
        now = time.time()
        ctx = self._ctx(messages_around(now), llm=StubLLM())
        result = await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(
            target_group_id=GROUP
        )
        self.assertIs(result["success"], True)
        self.assertIn(CANNED_SUMMARY, result["content"])
        self.assertIn(GROUP_NAME, result["content"])
        self.assertEqual(len(ctx.llm.calls), 1)
        record = summary_store.read_summary(self.db_path, TARGET_KEY)
        self.assertIsNotNone(record)
        self.assertEqual(record.text, CANNED_SUMMARY)
        self.assertEqual(record.target_type, "group")

    async def test_first_read_window_starts_window_hours_back(self) -> None:
        """spec 场景「首次概括取最近窗口」：输入起点为 now 减 window_hours。"""

        now = time.time()
        ctx = self._ctx(messages_around(now), llm=StubLLM())
        await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(target_group_id=GROUP)
        self.assertAlmostEqual(
            ctx.message.calls[0]["start_time"],
            now - WINDOW_HOURS * 3600,
            delta=5,
        )

    async def test_input_count_is_capped_by_config(self) -> None:
        now = time.time()
        ctx = self._ctx(messages_around(now), llm=StubLLM())
        await make_plugin(plugin_config([GROUP], max_input_messages=7), ctx).read_chat_summary(
            target_group_id=GROUP
        )
        self.assertEqual(ctx.message.calls[0]["limit"], 7)

    async def test_prompt_carries_the_target_and_the_messages(self) -> None:
        now = time.time()
        ctx = self._ctx(messages_around(now), llm=StubLLM())
        await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(target_group_id=GROUP)
        prompt = ctx.llm.calls[0]["prompt"]
        self.assertIn(GROUP_NAME, prompt)
        self.assertIn("消息0", prompt)

    async def test_generation_uses_the_planner_task(self) -> None:
        now = time.time()
        ctx = self._ctx(messages_around(now), llm=StubLLM())
        await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(target_group_id=GROUP)
        self.assertEqual(ctx.llm.calls[0]["task_name"], "planner")

    async def test_first_read_without_messages_does_not_call_the_model(self) -> None:
        ctx = self._ctx(messages=[], llm=StubLLM())
        result = await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(
            target_group_id=GROUP
        )
        self.assertIs(result["success"], False)
        self.assertIn("暂时没有可用的中期摘要", result["error"])
        self.assertEqual(ctx.llm.calls, [])
        self.assertIsNone(summary_store.read_summary(self.db_path, TARGET_KEY))


class StaleSummaryTests(SummaryChainTestCase):
    """spec 场景「过期摘要重新生成」：仅有过期摘要时重新概括并覆盖。"""

    async def test_stale_summary_is_regenerated_and_overwritten(self) -> None:
        now = time.time()
        stale_cutoff = now - 8 * 3600
        self._store(text="过期摘要", cutoff_ts=stale_cutoff, created_ts=now - 7 * 3600)
        ctx = self._ctx(messages_around(now), llm=StubLLM())
        result = await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(
            target_group_id=GROUP
        )
        self.assertIs(result["success"], True)
        self.assertIn(CANNED_SUMMARY, result["content"])
        self.assertNotIn("过期摘要", result["content"])
        record = summary_store.read_summary(self.db_path, TARGET_KEY)
        self.assertEqual(record.text, CANNED_SUMMARY)
        self.assertGreater(record.cutoff_ts, stale_cutoff)

    async def test_incremental_input_starts_at_previous_cutoff(self) -> None:
        """spec 场景「增量概括」：输入是上次截止时间之后的消息。"""

        now = time.time()
        stale_cutoff = now - 8 * 3600
        self._store(text="过期摘要", cutoff_ts=stale_cutoff, created_ts=now - 7 * 3600)
        ctx = self._ctx(messages_around(now), llm=StubLLM())
        await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(target_group_id=GROUP)
        self.assertEqual(ctx.message.calls[0]["start_time"], stale_cutoff)


class UnreadableTargetTests(SummaryChainTestCase):
    """spec 场景「不可读目标不生成」：不生成、不存储、不返回。"""

    async def test_group_outside_whitelist_is_rejected_without_generating(self) -> None:
        ctx = self._ctx(messages_around(time.time()), llm=StubLLM())
        result = await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(
            target_group_id=OTHER_GROUP
        )
        self.assertIs(result["success"], False)
        self.assertIn("不可读", result["error"])
        self.assertIn(OTHER_GROUP, result["error"])
        self.assertEqual(ctx.llm.calls, [])
        self.assertIsNone(summary_store.read_summary(self.db_path, f"group:{OTHER_GROUP}"))

    async def test_private_target_is_rejected_when_switch_off(self) -> None:
        ctx = self._ctx(messages_around(time.time()), llm=StubLLM())
        result = await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(
            target_user_id=PRIVATE_PEER
        )
        self.assertIs(result["success"], False)
        self.assertIn("不可读", result["error"])
        self.assertEqual(ctx.llm.calls, [])

    async def test_missing_target_is_reported_without_generating(self) -> None:
        ctx = StubContext(db=StubDatabase([]), llm=StubLLM(), data_dir=self.data_dir)
        result = await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(
            target_group_id=GROUP
        )
        self.assertIs(result["success"], False)
        self.assertIn("未找到", result["error"])
        self.assertEqual(ctx.llm.calls, [])

    async def test_invalid_argument_returns_the_dedicated_error(self) -> None:
        result = await make_plugin(plugin_config([GROUP]), self._ctx()).read_chat_summary()
        self.assertIs(result["success"], False)
        self.assertIn("二选一", result["error"])


class GenerationFailureTests(SummaryChainTestCase):
    """spec 场景「模型调用失败」：告知本次无法生成，旧摘要原样保留。"""

    async def test_failed_generation_keeps_the_previous_summary(self) -> None:
        now = time.time()
        stale_cutoff = now - 8 * 3600
        stale_created = now - 7 * 3600
        self._store(text="旧摘要", cutoff_ts=stale_cutoff, created_ts=stale_created)
        ctx = self._ctx(
            messages_around(now),
            llm=StubLLM(result={"success": False, "response": "模型不可用"}),
        )
        result = await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(
            target_group_id=GROUP
        )
        self.assertIs(result["success"], False)
        self.assertIn("无法生成", result["error"])
        self.assertIn("模型不可用", result["error"])
        record = summary_store.read_summary(self.db_path, TARGET_KEY)
        self.assertEqual(record.text, "旧摘要")
        self.assertEqual(record.cutoff_ts, stale_cutoff)
        self.assertEqual(record.created_ts, stale_created)

    async def test_failed_generation_without_previous_summary_says_unavailable(self) -> None:
        now = time.time()
        ctx = self._ctx(
            messages_around(now),
            llm=StubLLM(result={"success": False, "response": "模型不可用"}),
        )
        result = await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(
            target_group_id=GROUP
        )
        self.assertIs(result["success"], False)
        self.assertIn("暂时没有可用的中期摘要", result["error"])
        self.assertIsNone(summary_store.read_summary(self.db_path, TARGET_KEY))

    async def test_empty_model_text_is_treated_as_failure(self) -> None:
        now = time.time()
        ctx = self._ctx(
            messages_around(now),
            llm=StubLLM(result={"success": True, "response": "   "}),
        )
        result = await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(
            target_group_id=GROUP
        )
        self.assertIs(result["success"], False)
        self.assertIsNone(summary_store.read_summary(self.db_path, TARGET_KEY))

    async def test_raised_model_exception_is_reported(self) -> None:
        now = time.time()
        ctx = self._ctx(messages_around(now), llm=StubLLM(error=RuntimeError("连接被拒")))
        with self.assertLogs(summary_generator._LOGGER, level=logging.WARNING):
            result = await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(
                target_group_id=GROUP
            )
        self.assertIs(result["success"], False)
        self.assertIn("连接被拒", result["error"])

    async def test_unwritable_store_is_reported_without_returning_a_summary(self) -> None:
        """摘要库写不进去时告知本次无法生成，不返回虚构摘要。"""

        now = time.time()
        blocked = self.data_dir / "blocked"
        blocked.write_text("this is a file, not a directory", encoding="utf-8")
        ctx = StubContext(
            db=StubDatabase([group_row()]),
            message=StubMessage({SESSION_OF_GROUP: messages_around(now)}),
            llm=StubLLM(),
            data_dir=blocked / "nested",
        )
        result = await make_plugin(plugin_config([GROUP]), ctx).read_chat_summary(
            target_group_id=GROUP
        )
        self.assertIs(result["success"], False)
        self.assertIn("无法生成", result["error"])


class RetryTests(SummaryChainTestCase):
    """spec 场景「失败后可重试」：失败后再次调用按按需生成规则重新尝试。"""

    async def test_retry_after_failure_succeeds(self) -> None:
        now = time.time()
        failing = self._ctx(
            messages_around(now), llm=StubLLM(result={"success": False, "response": "模型不可用"})
        )
        plugin = make_plugin(plugin_config([GROUP]), failing)
        first = await plugin.read_chat_summary(target_group_id=GROUP)
        self.assertIs(first["success"], False)

        retry_ctx = self._ctx(messages_around(now), llm=StubLLM())
        plugin._set_context(retry_ctx)
        second = await plugin.read_chat_summary(target_group_id=GROUP)
        self.assertIs(second["success"], True)
        self.assertIn(CANNED_SUMMARY, second["content"])
        self.assertIsNotNone(summary_store.read_summary(self.db_path, TARGET_KEY))


if __name__ == "__main__":
    unittest.main()
