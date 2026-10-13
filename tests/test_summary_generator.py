"""任务 4.3：`summary_generator.py` 的 prompt 构建与模型调用测试。

两块接缝：prompt 构建与结果判读是纯函数（本机任意 Python 3 可跑，含不依赖 SDK 的
模型调用胶水用桩上下文驱动）；部署后的真实调用链路在任务 5.2/5.3。期望值取自
summary-generation spec「概括范围与模型」「生成失败不影响其他功能」的场景字面量。
"""

from __future__ import annotations

import logging
import unittest

from support import StubContext, StubLLM, load_plugin_module, message_dict

summary_generator = load_plugin_module("summary_generator")

LABEL = "测试群"
NOW = 1786000000.0
WINDOW_HOURS = 6


class SummaryInputStartTests(unittest.TestCase):
    """概括输入的起点：首次取最近 N 小时，已有摘要自上次 cutoff 起。"""

    def test_first_time_uses_window_hours_back(self) -> None:
        self.assertEqual(
            summary_generator.summary_input_start(None, NOW, WINDOW_HOURS),
            NOW - WINDOW_HOURS * 3600,
        )

    def test_existing_cutoff_wins(self) -> None:
        cutoff = NOW - 1234.0
        self.assertEqual(summary_generator.summary_input_start(cutoff, NOW, WINDOW_HOURS), cutoff)

    def test_zero_hours_first_time_is_now(self) -> None:
        self.assertEqual(summary_generator.summary_input_start(None, NOW, 0), NOW)

    def test_invalid_window_falls_back_to_now(self) -> None:
        self.assertEqual(summary_generator.summary_input_start(None, NOW, -3), NOW)

    def test_invalid_cutoff_falls_back_to_window(self) -> None:
        self.assertEqual(
            summary_generator.summary_input_start("not-a-time", NOW, WINDOW_HOURS),
            NOW - WINDOW_HOURS * 3600,
        )


class BuildSummaryPromptTests(unittest.TestCase):
    """prompt 构建：带目标标识、覆盖时间范围与逐条消息。"""

    def _prompt(self) -> str:
        messages = [
            message_dict(timestamp="1786000002.0", text="第三", message_id="m3", nickname="小红"),
            message_dict(timestamp="1786000000.0", text="第一", message_id="m1", nickname="小明"),
            message_dict(timestamp="1786000001.0", text="第二", message_id="m2", nickname="小刚"),
        ]
        return summary_generator.build_summary_prompt(LABEL, messages, NOW - 3600, NOW)

    def test_prompt_names_the_target(self) -> None:
        self.assertIn(LABEL, self._prompt())

    def test_prompt_carries_covered_time_range(self) -> None:
        prompt = self._prompt()
        self.assertIn(summary_generator.history_reader.format_timestamp(NOW - 3600), prompt)
        self.assertIn(summary_generator.history_reader.format_timestamp(NOW), prompt)

    def test_prompt_asks_for_a_summary(self) -> None:
        self.assertIn("摘要", self._prompt())

    def test_prompt_contains_every_message_line(self) -> None:
        prompt = self._prompt()
        for text in ("第一", "第二", "第三"):
            self.assertIn(text, prompt)

    def test_prompt_orders_messages_chronologically(self) -> None:
        prompt = self._prompt()
        self.assertLess(prompt.index("第一"), prompt.index("第二"))
        self.assertLess(prompt.index("第二"), prompt.index("第三"))

    def test_prompt_without_messages_is_still_built(self) -> None:
        prompt = summary_generator.build_summary_prompt(LABEL, [], NOW - 3600, NOW)
        self.assertIn(LABEL, prompt)


class ClassifyLLMResultTests(unittest.TestCase):
    """模型结果判读：可用文本返回文本，否则返回失败原因。"""

    def test_successful_text_is_returned(self) -> None:
        text, error = summary_generator.classify_llm_result(
            {"success": True, "response": "  这段时间在聊天气  "}
        )
        self.assertEqual(text, "这段时间在聊天气")
        self.assertEqual(error, "")

    def test_failed_success_flag_reports_reason_from_response(self) -> None:
        text, error = summary_generator.classify_llm_result(
            {"success": False, "response": "模型超时"}
        )
        self.assertEqual(text, "")
        self.assertIn("模型超时", error)

    def test_failed_success_flag_prefers_explicit_error(self) -> None:
        text, error = summary_generator.classify_llm_result(
            {"success": False, "error": "上游 500", "response": ""}
        )
        self.assertEqual(text, "")
        self.assertIn("上游 500", error)

    def test_empty_response_is_unusable(self) -> None:
        text, error = summary_generator.classify_llm_result({"success": True, "response": "   "})
        self.assertEqual(text, "")
        self.assertTrue(error)

    def test_non_mapping_is_unusable(self) -> None:
        text, error = summary_generator.classify_llm_result(None)
        self.assertEqual(text, "")
        self.assertTrue(error)


class GenerateSummaryTests(unittest.IsolatedAsyncioTestCase):
    """模型调用胶水：走 planner 任务，失败与异常都转成原因文本。"""

    async def test_successful_call_returns_text(self) -> None:
        llm = StubLLM(result={"success": True, "response": "罐头概括", "model": "planner"})
        text, error = await summary_generator.generate_summary(StubContext(llm=llm), "prompt")
        self.assertEqual(text, "罐头概括")
        self.assertEqual(error, "")

    async def test_call_uses_planner_task(self) -> None:
        llm = StubLLM()
        await summary_generator.generate_summary(StubContext(llm=llm), "prompt")
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.calls[0]["task_name"], summary_generator.PLANNER_TASK_NAME)
        self.assertEqual(llm.calls[0]["prompt"], "prompt")

    async def test_failed_result_returns_reason(self) -> None:
        llm = StubLLM(result={"success": False, "response": "模型不可用"})
        text, error = await summary_generator.generate_summary(StubContext(llm=llm), "prompt")
        self.assertEqual(text, "")
        self.assertIn("模型不可用", error)

    async def test_raised_exception_returns_reason(self) -> None:
        llm = StubLLM(error=RuntimeError("连接被拒"))
        with self.assertLogs(summary_generator._LOGGER, level=logging.WARNING):
            text, error = await summary_generator.generate_summary(StubContext(llm=llm), "prompt")
        self.assertEqual(text, "")
        self.assertIn("连接被拒", error)

    async def test_missing_llm_capability_returns_reason(self) -> None:
        class NoLlmContext:
            """没有任何模型能力的上下文：只有 logger。"""

            logger = None

        text, error = await summary_generator.generate_summary(NoLlmContext(), "prompt")
        self.assertEqual(text, "")
        self.assertTrue(error)


class FailureMessageTests(unittest.TestCase):
    """失败文案：区分「从未有摘要」与「旧摘要已过期」。"""

    def test_missing_summary_message_says_temporarily_unavailable(self) -> None:
        message = summary_generator.summary_failure_message(LABEL, "模型不可用")
        self.assertIn(LABEL, message)
        self.assertIn("暂时没有可用的中期摘要", message)
        self.assertIn("模型不可用", message)

    def test_stale_summary_message_mentions_previous_cutoff(self) -> None:
        cutoff = NOW - 7200
        message = summary_generator.summary_failure_message(LABEL, "模型不可用", cutoff)
        self.assertIn(LABEL, message)
        self.assertIn("模型不可用", message)
        self.assertIn(summary_generator.history_reader.format_timestamp(cutoff), message)

    def test_missing_and_stale_branches_use_their_own_wording(self) -> None:
        """真实契约：无 cutoff 走「从未有摘要」分支，带 cutoff 走「已过期」分支。"""

        without_cutoff = summary_generator.summary_failure_message(LABEL, "模型不可用")
        with_cutoff = summary_generator.summary_failure_message(LABEL, "模型不可用", NOW - 7200)
        self.assertIn("暂时没有可用的中期摘要", without_cutoff)
        self.assertIn("已过期", with_cutoff)
        self.assertNotEqual(without_cutoff, with_cutoff)


if __name__ == "__main__":
    unittest.main()
