"""中期摘要的 prompt 构建与 planner 模型调用胶水。

本模块不 import maibot_sdk：prompt 构建、输入起点计算与结果判读都是纯函数，本机可
单测；模型调用走鸭子类型的 `ctx.llm.generate`，测试用桩上下文驱动。概括统一用宿主的
planner 模型任务，`request_type` 由宿主落到 `plugin.<插件 id>`（见调研 Q2/Q9）。

输入范围：目标已有截止时间为 T 的摘要时取 T 之后的消息，首次取最近 `window_hours`
小时；条数上限由调用方按 `max_input_messages` 传给读取路径。失败一律转成原因文本，
不写坏数据进存储（`read_chat_summary` 只在拿到可用文本后才落盘）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any, Final

from . import history_reader
from .coercion import as_float, clean_str

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)
"""模块日志：模型调用抛异常时记一笔，便于定位失败原因。"""

PLANNER_TASK_NAME: Final[str] = "planner"
"""概括使用的宿主模型任务名。"""

NO_MESSAGES_REASON: Final[str] = "概括时间范围内没有可用的消息"
"""概括范围内没有任何消息时的失败原因（不调模型，避免凭空生成）。"""

STORE_UNAVAILABLE_REASON: Final[str] = "摘要存储不可用"
"""摘要库拿不到或写入失败时的失败原因。"""

PROMPT_INSTRUCTION: Final[str] = (
    "请概括下面这段聊天记录，输出一段中文摘要：突出主要话题、结论与待办，"
    "不要逐条复述，也不要编造记录里没有的内容。"
)

PROMPT_HEADER_TEMPLATE: Final[str] = "目标「{label}」，时间范围 {start} 至 {end}。"
"""prompt 头部：目标标识与覆盖的时间范围。"""

PROMPT_RECORDS_LABEL: Final[str] = "聊天记录："

MISSING_FAILURE_TEMPLATE: Final[str] = (
    "目标「{label}」暂时没有可用的中期摘要：本次无法生成摘要（{reason}），请稍后再试。"
)

STALE_FAILURE_TEMPLATE: Final[str] = (
    "目标「{label}」的中期摘要已过期，本次无法生成新的摘要（{reason}）；"
    "上次的摘要仍保留（覆盖至 {cutoff}），请稍后再试。"
)


def summary_input_start(existing_cutoff: object, now: float, window_hours: object) -> float:
    """概括输入的起点：已有摘要自其 cutoff 起，首次回溯 `window_hours` 小时。

    `existing_cutoff` 非法或缺失时按首次处理；`window_hours` 非法或为负时取 0（即
    只覆盖当前时刻）。
    """

    cutoff = as_float(existing_cutoff)
    if cutoff is not None:
        return cutoff
    hours = as_float(window_hours)
    if hours is None or hours < 0:
        hours = 0.0
    return float(now) - hours * 3600.0


def build_summary_prompt(
    label: str,
    messages: Iterable[Mapping[str, Any]],
    start_ts: object,
    end_ts: object,
) -> str:
    """构建概括 prompt：目标标识、覆盖时间范围、逐条消息与概括指令。

    消息按时间正序渲染（复用 `history_reader` 的行格式），让模型读到时间顺序正确的
    上下文；没有消息时仍产出一个合法的 prompt（调用方通常已先拦下空输入）。
    """

    lines = [
        PROMPT_INSTRUCTION,
        PROMPT_HEADER_TEMPLATE.format(
            label=label,
            start=history_reader.format_timestamp(start_ts),
            end=history_reader.format_timestamp(end_ts),
        ),
        PROMPT_RECORDS_LABEL,
    ]
    lines.extend(
        history_reader.message_line(message) for message in history_reader.sorted_messages(messages)
    )
    return "\n".join(lines)


def classify_llm_result(result: object) -> tuple[str, str]:
    """判读 `llm.generate` 的返回：返回 `(摘要文本, 失败原因)`，二者互斥。

    成功且 `response` 有非空文本时返回文本；`success` 非真、结果不是映射或文本为空时
    返回空文本加原因（优先取 `error`，其次 `response`）。
    """

    if not isinstance(result, Mapping):
        return "", "模型没有返回可用的结果"
    if result.get("success") is not True:
        reason = clean_str(result.get("error")) or clean_str(result.get("response")) or "模型调用未成功"
        return "", reason
    text = clean_str(result.get("response"))
    if not text:
        return "", "模型返回值里没有摘要文本"
    return text, ""


async def generate_summary(ctx: object, prompt: str) -> tuple[str, str]:
    """调 planner 概括 `prompt`：返回 `(摘要文本, 失败原因)`。

    调用走 `ctx.llm.generate(prompt, task_name="planner")`；能力缺失、抛异常或结果
    不可用都转成原因文本，由调用方决定是否落盘。
    """

    llm = getattr(ctx, "llm", None)
    generate = getattr(llm, "generate", None)
    if generate is None:
        return "", "插件上下文没有可用的模型能力"
    try:
        result = await generate(prompt, task_name=PLANNER_TASK_NAME)
    except Exception as exc:
        _LOGGER.warning("planner 概括调用失败", exc_info=True)
        return "", f"调用模型失败：{exc}"
    return classify_llm_result(result)


def summary_failure_message(label: str, reason: str, previous_cutoff: object = None) -> str:
    """生成失败时的告知文案，区分「从未有摘要」与「旧摘要已过期」。"""

    cutoff = as_float(previous_cutoff)
    if cutoff is None:
        return MISSING_FAILURE_TEMPLATE.format(label=label, reason=reason)
    return STALE_FAILURE_TEMPLATE.format(
        label=label, reason=reason, cutoff=history_reader.format_timestamp(cutoff)
    )
