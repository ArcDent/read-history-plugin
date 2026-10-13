"""目标参数的解析与四类错误文案。

本模块是纯逻辑（不 import maibot_sdk、不碰网络），本机任意 Python 3 可单测。两个读取
工具共用一对目标参数（`target_group_id` 与 `target_user_id`），必须且只能提供一个；
错误文案要能让 LLM 自我纠正，因此每条都说明正确用法并建议先调 `list_readable_streams`。

四类错误各自独立、互不歧义：两个同缺、两个同在、格式非法（非数字串或空串）、目标不
存在（格式合法但宿主没有对应会话）。前四类里「目标不存在」由解析层判断后取这里是文案。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from .coercion import clean_str
from .config_rules import is_numeric_stream_id

GROUP_ARGUMENT: Final[str] = "target_group_id"
"""目标群号参数名。"""

USER_ARGUMENT: Final[str] = "target_user_id"
"""目标私聊对端 QQ 号参数名。"""

TARGET_TYPE_GROUP: Final[str] = "group"
"""群聊目标的类型取值：`target_params` 与 `stream_resolver` 共用这一套词汇。"""

TARGET_TYPE_PRIVATE: Final[str] = "private"
"""私聊目标的类型取值；摘要库的 `target_type` 列也用这两个值。"""

_SUGGEST: Final[str] = "可先调用 list_readable_streams 获取合法目标。"

MISSING_BOTH_ERROR: Final[str] = (
    f"参数错误：{GROUP_ARGUMENT} 与 {USER_ARGUMENT} 必须二选一，当前两个都没有提供。"
    f"请提供要读取的群号或 QQ 号，并只给其中一个。{_SUGGEST}"
)

BOTH_PRESENT_ERROR: Final[str] = (
    f"参数错误：{GROUP_ARGUMENT} 与 {USER_ARGUMENT} 必须二选一，当前两个都提供了。"
    f"请只保留你要读取的那一个，另一个不要传。{_SUGGEST}"
)

INVALID_FORMAT_ERROR: Final[str] = (
    f"参数错误：目标参数必须是去空白后的非空数字字符串（群号或 QQ 号）。"
    f"请检查是否传了字母、符号或空串。{_SUGGEST}"
)

NOT_FOUND_TEMPLATE: Final[str] = (
    "未找到该聊天流：宿主没有 {kind}「{value}」对应的会话记录。"
    "请确认号码正确，或换一个目标。{suggest}"
)

UNREADABLE_GROUP_TEMPLATE: Final[str] = (
    "目标不可读：群「{value}」不在可读白名单内，本插件只允许读取白名单里的群。"
    "如确需读取，请让管理员把该群号加入白名单。{suggest}"
)

UNREADABLE_PRIVATE_ERROR: Final[str] = (
    "目标不可读：私聊历史总闸当前关闭，读不到任何私聊历史。"
    "如确需读取，请让管理员打开私聊开关。{suggest}"
)


@dataclass(frozen=True)
class Target:
    """解析后的目标：`group`（群聊）或 `private`（私聊），值为群号或 QQ 号。"""

    target_type: str
    target_id: str


def parse_target(arguments: Mapping[str, object]) -> tuple[Target | None, str]:
    """解析两个目标参数，返回 `(目标, 错误文案)`；成功时错误文案为空串。

    规则：两个都缺或都在都算错误；只给一个时，值必须是字符串（或整数，字符串化后判定）
    且去空白后为非空数字串。`None` 视为未提供，空串视为「提供了但格式非法」。
    """

    args = arguments if isinstance(arguments, Mapping) else {}
    has_group = _provided(args, GROUP_ARGUMENT)
    has_user = _provided(args, USER_ARGUMENT)

    if has_group and has_user:
        return None, BOTH_PRESENT_ERROR
    if not has_group and not has_user:
        return None, MISSING_BOTH_ERROR

    key = GROUP_ARGUMENT if has_group else USER_ARGUMENT
    value = _normalize(args.get(key))
    if not value or not is_numeric_stream_id(value):
        return None, INVALID_FORMAT_ERROR

    kind = TARGET_TYPE_GROUP if has_group else TARGET_TYPE_PRIVATE
    return Target(kind, value), ""


def not_found_error(target: Target) -> str:
    """目标格式合法但宿主没有会话记录时的文案。"""

    kind = "群号" if target.target_type == TARGET_TYPE_GROUP else "QQ 号"
    return NOT_FOUND_TEMPLATE.format(kind=kind, value=target.target_id, suggest=_SUGGEST)


def unreadable_group_error(group_id: str) -> str:
    """目标群不在白名单内时的文案。"""

    return UNREADABLE_GROUP_TEMPLATE.format(value=group_id, suggest=_SUGGEST)


def unreadable_private_error() -> str:
    """私聊开关关闭时的文案。"""

    return UNREADABLE_PRIVATE_ERROR.format(suggest=_SUGGEST)


def _provided(arguments: Mapping[str, object], key: str) -> bool:
    """参数是否「被提供」：键存在且值不是 None（空串算提供了，走格式非法分支）。"""

    return key in arguments and arguments[key] is not None


def _normalize(value: object) -> str | None:
    """把参数值规整成待判定的字符串；不能当作号码文本的一律 None。"""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return clean_str(value)
    return None
