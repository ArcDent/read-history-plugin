"""门禁判定：白名单与私聊开关决定哪些群暴露工具、哪些聊天流可读。

本模块是纯逻辑：不 import maibot_sdk、不读文件、不碰网络，本机任意 Python 3 直接
import。三条规则对应 spec `chat-access-control`：

1. 群聊是双重条件同源：群号在白名单内，该群才暴露工具，且作为读取目标才可读；
   不在则两者皆假。暴露面由 `allowed_session` 承载（见 `stream_resolver.py`），
   本模块负责可读侧判定。
2. 私聊不暴露工具（由组件层 `chat_scope="group"` 保证）；作为读取目标，可读性只由
   私聊开关决定，与白名单内容无关。白名单只装群号，里面写 QQ 号不产生任何作用：
   既不改变私聊可读性，也不让私聊暴露工具（ADR-0003）。
3. access 节（白名单与私聊开关）签名变化时调度一次插件自重载，让暴露面按新配置
   重建；重载失败只记日志，不抛出、不翻转现状。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any, Final

from .coercion import clean_str
from .config_rules import normalize_stream_entries

ACCESS_SECTION: Final[str] = "access"
"""配置里承载白名单与私聊开关的节名。"""

READABLE_STREAMS_KEY: Final[str] = "readable_streams"
"""配置里白名单字段的键名。"""

PRIVATE_ENABLED_KEY: Final[str] = "private_enabled"
"""配置里私聊开关字段的键名。"""

CONFIG_RELOAD_SCOPE_SELF: Final[str] = "self"
"""`on_config_update` 里表示「本插件自己的 config.toml 变了」的范围值。"""

AccessSignature = tuple[tuple[str, ...], bool]
"""access 节的可比签名：`(归一化白名单, 私聊开关)`。"""


def _entry_set(entries: Iterable[object]) -> frozenset[str]:
    """把白名单条目折成可比集合；条目非法时整份按空集合处理（fail-closed）。"""

    normalized, rejection = normalize_stream_entries(entries)
    return frozenset() if rejection else frozenset(normalized)


def group_is_readable(entries: Iterable[object], group_id: object) -> bool:
    """群作为读取目标是否可读：群号在白名单内。

    判定不看私聊开关：开关只管私聊历史，不该顺带影响群的暴露与可读。
    """

    target = clean_str(group_id)
    return bool(target) and target in _entry_set(entries)


def private_is_readable(switch_on: bool) -> bool:
    """私聊作为读取目标是否可读：只看私聊开关，与白名单内容无关。

    开关打开时全部有历史记录的私聊都成为可读目标（目标是否存在由解析层确认），
    关闭时任何私聊历史都读不到。白名单只装群号，写进名单的 QQ 号既不构成授权，
    也不构成限制（ADR-0003）。
    """

    return switch_on is True


def readable_entries(config_data: Mapping[str, Any]) -> list[str]:
    """从配置字典的 access 节取归一化白名单；形态不对或条目非法一律按空清单。

    空白名单与「读不到配置」都归到空清单，调用方据此 fail-closed。
    """

    section = config_data.get(ACCESS_SECTION) if isinstance(config_data, Mapping) else None
    if not isinstance(section, Mapping):
        return []
    raw_entries = section.get(READABLE_STREAMS_KEY)
    if not isinstance(raw_entries, (list, tuple)):
        return []
    normalized, rejection = normalize_stream_entries(raw_entries)
    return [] if rejection else normalized


def private_enabled(config_data: Mapping[str, Any]) -> bool:
    """从配置字典的 access 节取私聊开关；非布尔一律按关闭。"""

    section = config_data.get(ACCESS_SECTION) if isinstance(config_data, Mapping) else None
    if not isinstance(section, Mapping):
        return False
    return section.get(PRIVATE_ENABLED_KEY) is True


def access_signature(config_data: Mapping[str, Any]) -> AccessSignature:
    """取 access 节的可比签名：只看白名单与私聊开关，其它节的变化不参与比较。"""

    return (tuple(readable_entries(config_data)), private_enabled(config_data))


async def reload_on_access_change(
    *,
    scope: str,
    config_data: Mapping[str, Any],
    previous_signature: AccessSignature | None,
    reload_plugin: Callable[[], Awaitable[Any]],
    logger: logging.Logger,
) -> AccessSignature | None:
    """配置热更新的门禁处理：access 节签名变化时调用一次 `reload_plugin`。

    四种情况的约定：非 `self` 范围（bot/model 全局广播）原样返回上次签名；首次调用
    只登记基线不重载；签名未变不重载；签名变化调用一次。重载抛出的异常只记日志、
    不向上抛，已注册组件与暴露面保持重载前的状态。

    Returns:
        归一化后的本次签名，供下次比较；非 `self` 范围返回上次签名。
    """

    if scope != CONFIG_RELOAD_SCOPE_SELF:
        return previous_signature

    signature = access_signature(config_data)
    if signature == previous_signature or previous_signature is None:
        return signature

    try:
        await reload_plugin()
    except Exception:
        logger.warning(
            "access 配置已变更但插件自重载失败，保持重载前的暴露面；"
            "改回旧配置或重启插件即可恢复",
            exc_info=True,
        )
    return signature
