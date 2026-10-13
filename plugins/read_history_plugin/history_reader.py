"""历史读取的纯渲染与条数钳制，以及读取能力的胶水调用。

本模块的渲染与钳制是纯逻辑（不 import maibot_sdk、不碰网络），本机任意 Python 3 可单测；
`fetch_recent_messages` 是鸭子类型的能力胶水，测试用桩上下文驱动。

渲染用自绘而不是 `build_readable`：后者的时间戳默认相对化，而且没法带上目标标识；跨群
场景里 LLM 必须知道正在读的是哪个群，所以输出头部固定带目标标识，行格式统一为
`[时间] 昵称：内容`。
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any, Final

from .stream_resolver import rows_of, user_id_of, user_name_of

LIST_HEADER: Final[str] = "当前可读取的聊天流："
"""可读清单的输出头部。"""

LIST_EMPTY: Final[str] = "当前没有可读取的聊天流。"
"""没有任何可读目标时的提示。"""

PRIVATE_TRUNCATION_NOTE: Final[str] = "私聊目标较多，仅列出最近活跃的前 {limit} 个。"
"""私聊目标被截断时的说明（`{limit}` 为代码里的数量上限）。"""

UNCONFIRMED_ENTRIES_NOTE: Final[str] = "白名单中有 {count} 个条目未被确认为群，不会生效。"
"""白名单里未确认为群的条目数量提示（`{count}` 为条数）；为零时不输出。"""

NO_MESSAGES_TEMPLATE: Final[str] = "目标「{label}」没有可查的上下文消息。"
"""目标存在但没有消息记录时的提示。"""

SUMMARY_HEADER_TEMPLATE: Final[str] = "目标「{label}」的中期摘要（覆盖至 {cutoff}，生成于 {created}）："
"""中期摘要的输出头部，带目标标识与摘要覆盖到的时间。"""

HISTORY_HEADER_TEMPLATE: Final[str] = "目标「{label}」的最近 {count} 条消息（按时间正序）："
"""历史消息的输出头部，带目标标识与条数。"""

NON_TEXT_PLACEHOLDER: Final[str] = "[非文本消息]"
"""消息没有纯文本内容时的占位，避免整条被隐藏。"""


def clamp_limit(requested: object, default_limit: int, max_limit: int) -> int:
    """把请求条数钳制到配置允许的范围。

    缺省、非整数、非正数一律退回默认条数；超过上限按上限；结果再与上限取小，防止默认
    值本身大于上限。布尔值不算整数（`True`/`False` 退回默认）。
    """

    resolved = _as_positive_int(requested)
    if resolved is None:
        resolved = max(1, int(default_limit))
    ceiling = max(1, int(max_limit))
    return min(resolved, ceiling)


def _as_positive_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit() and int(text) > 0:
            return int(text)
    return None


def format_timestamp(value: object) -> str:
    """把消息时间戳（epoch 秒，宿主以字符串给出）格式化成 `YYYY-MM-DD HH:MM:SS`。

    解析失败时原样回显，保证渲染不因个别脏数据整段失败。
    """

    try:
        # 以 UTC 构造再转本地时区，避免依赖进程默认时区的隐式行为。
        moment = datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone()
        return moment.strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(value) if value is not None else ""


def message_sender(message: Mapping[str, Any]) -> str:
    """取消息发送者显示名：备注优先，其次昵称，再退回 QQ 号。"""

    info = message.get("message_info")
    user = info.get("user_info") if isinstance(info, Mapping) else None
    if isinstance(user, Mapping):
        for key in ("user_cardname", "user_nickname", "user_id"):
            value = user.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "未知用户"


def message_text(message: Mapping[str, Any]) -> str:
    """取消息正文：纯文本字段，缺失时用占位符。"""

    value = message.get("processed_plain_text")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return NON_TEXT_PLACEHOLDER


def message_line(message: Mapping[str, Any]) -> str:
    """把一条消息渲染成 `[时间] 昵称：内容`。"""

    return f"[{format_timestamp(message.get('timestamp'))}] {message_sender(message)}：{message_text(message)}"


def _timestamp_sort_key(message: Mapping[str, Any]) -> float:
    try:
        return float(message.get("timestamp"))
    except (TypeError, ValueError):
        return float("inf")


def sorted_messages(messages: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """按时间正序排列消息；没有可用时间戳的排在最后（稳定顺序）。"""

    return sorted(messages, key=_timestamp_sort_key)


def render_history(label: str, messages: Iterable[Mapping[str, Any]]) -> str:
    """渲染历史消息：头部带目标标识，正文按时间正序逐行。"""

    ordered = sorted_messages(messages)
    lines = [HISTORY_HEADER_TEMPLATE.format(label=label, count=len(ordered))]
    lines.extend(message_line(message) for message in ordered)
    return "\n".join(lines)


def render_no_messages(label: str) -> str:
    """渲染「目标存在但没有消息」的提示。"""

    return NO_MESSAGES_TEMPLATE.format(label=label)


def render_summary(label: str, text: str, cutoff_ts: object, created_ts: object) -> str:
    """渲染中期摘要：头部带目标标识与覆盖到的时间，正文是概括文本。

    `cutoff_ts` 是这条摘要覆盖到的时刻，`created_ts` 是生成时刻；两者都格式化成可读
    时间，让 LLM 知道这段概括对应哪个时间范围。
    """

    header = SUMMARY_HEADER_TEMPLATE.format(
        label=label,
        cutoff=format_timestamp(cutoff_ts),
        created=format_timestamp(created_ts),
    )
    return f"{header}\n{text}"


def render_stream_list(
    group_entries: Iterable[str],
    name_by_group: Mapping[str, str],
    private_rows: Iterable[Mapping[str, Any]],
    private_limit: int,
    unconfirmed_count: int = 0,
) -> str:
    """渲染可读目标清单：群按白名单顺序逐条，私聊按最近活跃顺序截断。

    每项含目标类型、群号或 QQ 号与名称；群名或用户名称取不到时用号兜底。`group_entries`
    只装已确认为群的条目（未确认的由调用方先滤掉，只把数量传进 `unconfirmed_count`），
    所以白名单里写错的号不会作为群聊行出现；`unconfirmed_count` 大于零时在清单末尾另加
    一行提示其数量与不会生效。私聊条目过多时只保留前 `private_limit` 个，并在末尾说明截断。
    """

    lines: list[str] = []
    for group_id in group_entries:
        name = name_by_group.get(group_id) or group_id
        lines.append(f"- 群聊 {group_id}（{name}）")

    private_list = list(private_rows)
    for row in private_list[: max(0, int(private_limit))]:
        user_id = user_id_of(row)
        if not user_id:
            continue
        name = user_name_of(row) or user_id
        lines.append(f"- 私聊 {user_id}（{name}）")

    output = [LIST_HEADER, *lines] if lines else [LIST_EMPTY]
    unconfirmed = max(0, int(unconfirmed_count))
    if unconfirmed > 0:
        output.append(UNCONFIRMED_ENTRIES_NOTE.format(count=unconfirmed))
    if len(private_list) > max(0, int(private_limit)):
        output.append(PRIVATE_TRUNCATION_NOTE.format(limit=int(private_limit)))
    return "\n".join(output)


async def fetch_recent_messages(
    ctx: object,
    session_id: str,
    limit: int,
    include_bot_messages: bool,
    start_time: float = 0,
) -> list[Mapping[str, Any]]:
    """按会话读取 `start_time` 起最近 limit 条消息。

    用 `get_by_time_in_chat` 配宽时间窗绕开 `get_recent` 的 24 小时隐形窗口；
    `limit_mode="latest"` 取最新若干条，`filter_command=True` 恒定过滤命令消息，
    `filter_mai` 按配置决定是否过滤机器人发言。`start_time` 缺省为 0（读全部历史），
    中期摘要传上次截止时间以只取增量。
    """

    message = getattr(ctx, "message", None)
    method = getattr(message, "get_by_time_in_chat", None)
    if method is None:
        return []
    result = await method(
        chat_id=session_id,
        start_time=start_time,
        end_time=_now(),
        limit=limit,
        limit_mode="latest",
        filter_mai=not include_bot_messages,
        filter_command=True,
    )
    return rows_of(result)


def _now() -> float:
    return time.time()
