"""配置取值的纯规则：白名单条目的归一化与合法性判定。

本模块是「什么样的白名单条目合法」与「合法条目长什么样」的单一定义处，
`normalize_stream_entries` 一次返回「归一化清单 + 拒绝原因」，被三处消费：`config.py`
在配置期据此整份拒绝非法白名单，`access_gate.py` 据此判定群的可读性，`stream_resolver.py`
据此投影 `allowed_session`。三处对「合法」的认定因此不可能分歧。本模块只做字面量
判定：不 import maibot_sdk、不 import 第三方包、不读文件、不碰网络，保证在开发机上
用任意 Python 3 都能跑。

群号统一按 ASCII 十进制数字串判定。宿主侧 `allowed_session` 的候选集是字符串集合，
全角数字、带空白或带符号的文本即使被当作数字也无法命中，属于配了不生效的坑，所以在
配置期直接拒绝而不是静默接受。白名单只装群号：写其它数字串（例如 QQ 号）能通过格式
校验但不产生作用，既不让私聊暴露工具，也不改变私聊可读性。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

_NUMERIC_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9]+\Z")
"""数字号码的文法：至少一位 ASCII 十进制数字，不含符号、空白与全角数字。"""


def is_numeric_stream_id(text: str) -> bool:
    """判定已去空白的文本是否为 ASCII 十进制数字串。

    群号与 QQ 号共用这一条格式判定（数值空间不同，语义由调用方决定）；全角数字
    （如 `１９１９８１０`）与带空白的文本都不合法。本函数不做去空白，去空白是调用方
    的前置步骤。
    """

    return bool(_NUMERIC_ID_PATTERN.match(text))


def normalize_stream_entries(values: Iterable[object]) -> tuple[list[str], str]:
    """归一化白名单并判定合法性，返回 `(归一化清单, 拒绝原因)`。

    合法性的定义只有这一处：条目必须是字符串、去空白后非空、且是数字串。任何一条
    不满足就整份拒绝，返回 `([], 原因)`，原因里带 1 起数的位置；这不只是给运维看的
    文案，也保证调用方忽略原因时拿到的空清单天然 fail-closed，不会静默留下半个白名单。

    全部合法时返回去空白、按首次出现顺序去重的清单与空原因：这个清单可直接用作
    `allowed_session` 与群可读性判定的输入。
    """

    normalized: list[str] = []
    for position, value in enumerate(values, start=1):
        if not isinstance(value, str):
            return [], f"第 {position} 个可读群号条目必须是字符串，收到 {type(value).__name__}"
        entry = value.strip()
        if not entry:
            return [], f"第 {position} 个可读群号条目去空白后不能为空"
        if not is_numeric_stream_id(entry):
            return [], f"第 {position} 个可读群号条目「{entry}」必须是 ASCII 十进制数字串（群号）"
        if entry not in normalized:
            normalized.append(entry)
    return normalized, ""
