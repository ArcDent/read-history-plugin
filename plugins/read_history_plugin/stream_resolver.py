"""白名单条目到宿主会话的解析，以及 `allowed_session` 的投影。

本模块不 import maibot_sdk、不读网络，本机任意 Python 3 直接 import。宿主能力通过
鸭子类型的 `ctx`（`ctx.db` / `ctx.chat`）访问，测试可用桩上下文驱动。

三件事：

1. 号到会话的解析（任务 3.1）：目标参数是群号或 QQ 号，宿主内部用的是 32 位十六进制
   session_id。优先 `database.get("ChatSession", filters=…)` 查库，不受会话活跃度限制；
   未命中再用 `chat.get_stream_by_group_id` / `get_stream_by_user_id` 兜底（只扫内存里
   当前活跃的会话）；两者都空即「目标不存在」。同群可有多个 session（scope 维度），
   一律取 `last_active_timestamp` 最近活跃的一条。
2. 暴露面的群号确认：`allowed_session` 的候选集是字符串集合，填群号即命中；但白名单
   条目都是纯数字串，格式上分不出群号与 QQ 号。写错的 QQ 号一旦等于某个群的 group_id，
   那个非白名单群就会意外拿到工具。所以只回「经会话表确认为群」的条目。确认结果放在
   插件 data_dir 的快照文件里跨自重载保留：`get_components()` 是同步的、拿不到异步的
   库查询，只能读上次 `on_load` / `on_config_update` 落盘的快照。
3. 可读目标的清单素材：群会话与私聊会话的行清单（供 `list_readable_streams` 取名称）。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .coercion import clean_str, path_in_dir
from .config_rules import normalize_stream_entries
from .target_params import TARGET_TYPE_GROUP, TARGET_TYPE_PRIVATE

_LOGGER = logging.getLogger(__name__)
"""模块日志：宿主能力缺失或调用失败时记一笔，便于定位兜底路径。"""

CHAT_SESSION_MODEL: str = "ChatSession"
"""解析群号/QQ 号所用的宿主模型名。"""

LAST_ACTIVE_FIELD: str = "last_active_timestamp"
"""会话最近活跃时间字段名；同群多 session 时据此取最新。"""

KNOWN_GROUPS_FILENAME: str = "known_groups.json"
"""已确认为群的群号快照文件名（落在插件 data_dir 下）。"""

_known_group_ids: frozenset[str] = frozenset()
"""进程内缓存的已确认群号集合；由 `set_known_group_ids` 从快照刷新。"""


# --------------------------------------------------------------------- 基础取值


def rows_of(result: object) -> list[Mapping[str, Any]]:
    """把宿主返回值规整成行清单：单行映射包成单元素列表，其余非法值按空清单。"""

    if isinstance(result, Mapping):
        return [result]
    if isinstance(result, (list, tuple)):
        return [row for row in result if isinstance(row, Mapping)]
    return []


def session_id_of(row: object) -> str:
    """取会话行或 stream 结构里的 session_id；`stream_id` 是等价别名。"""

    if not isinstance(row, Mapping):
        return ""
    return clean_str(row.get("session_id")) or clean_str(row.get("stream_id"))


def group_id_of(row: object) -> str:
    """取会话行里的群号；私聊行为空串。"""

    return clean_str(row.get("group_id")) if isinstance(row, Mapping) else ""


def user_id_of(row: object) -> str:
    """取会话行里的用户 QQ 号。"""

    return clean_str(row.get("user_id")) if isinstance(row, Mapping) else ""


def group_name_of(row: object) -> str:
    """取会话行里的群名；取不到为空串（调用方用群号兜底）。"""

    return clean_str(row.get("group_name")) if isinstance(row, Mapping) else ""


def user_name_of(row: object) -> str:
    """取会话行里的用户名称：备注优先，其次昵称。"""

    if not isinstance(row, Mapping):
        return ""
    return clean_str(row.get("user_cardname")) or clean_str(row.get("user_nickname"))


def _last_active_key(row: Mapping[str, Any]) -> str:
    """取排序键：`last_active_timestamp`。

    宿主把 datetime 序列化成 ISO 字符串，同格式下字典序即时间序；缺字段或非法值返回
    空串，空串排在所有 ISO 时间之前，于是没有时间信息的行被当作最旧。
    """

    return clean_str(row.get(LAST_ACTIVE_FIELD))


def pick_most_active_session(rows: Iterable[object]) -> Mapping[str, Any] | None:
    """从候选会话行里挑最近活跃的一条。

    - 空清单返回 None。
    - 没有 session_id 的行直接跳过（无法寻址）。
    - 有 session_id 但没有有效时间的行仍可用，只是按最旧参与比较。
    - 时间相同的多行，取输入顺序里靠前的那条（`max` 的稳定语义），行为确定。
    """

    candidates = [row for row in rows if isinstance(row, Mapping) and session_id_of(row)]
    if not candidates:
        return None
    return max(candidates, key=_last_active_key)


def group_ids_from_rows(rows: Iterable[object]) -> list[str]:
    """从会话行清单里收集群号（保持出现顺序去重）。"""

    seen: list[str] = []
    for row in rows:
        group_id = group_id_of(row)
        if group_id and group_id not in seen:
            seen.append(group_id)
    return seen


# --------------------------------------------------------------------- 群号快照


def read_known_group_ids(data_dir: object) -> list[str]:
    """从快照文件读已确认群号；文件缺失或内容非法一律按空清单（fail-closed）。

    内容损坏（非法 JSON 或结构不对）在返回空清单的同时记一条 debug 日志：空清单会让
    暴露面 fail-closed，不记日志的话一次静默的读失败会被误当成「本来就没有已确认群」。
    """

    path = path_in_dir(data_dir, KNOWN_GROUPS_FILENAME)
    if path is None or not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _LOGGER.debug("读取群号快照失败，按空清单处理（暴露面 fail-closed）：%s", path, exc_info=True)
        return []
    if not isinstance(payload, list):
        _LOGGER.debug("群号快照结构不是列表，按空清单处理：%s", path)
        return []
    result: list[str] = []
    for value in payload:
        group_id = clean_str(value)
        if group_id and group_id not in result:
            result.append(group_id)
    return result


def write_known_group_ids(data_dir: object, group_ids: Iterable[object]) -> None:
    """把已确认群号原子写入快照文件；目录不存在时创建。

    先写同目录下的临时文件再 `os.replace`，避免 `path.write_text` 中途失败或与下次
    读取竞争时留下半截 JSON——半截文件会让下一次读取静默退回空清单、一次性摘掉全部
    工具的暴露面。
    """

    path = path_in_dir(data_dir, KNOWN_GROUPS_FILENAME)
    if path is None:
        return
    normalized: list[str] = []
    for value in group_ids:
        group_id = clean_str(value)
        if group_id and group_id not in normalized:
            normalized.append(group_id)
    payload = json.dumps(normalized, ensure_ascii=False)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(payload, encoding="utf-8")
        os.replace(temp_path, path)
    except OSError:
        _LOGGER.warning("写入群号快照失败：%s", path, exc_info=True)
        try:
            temp_path.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------- 解析接缝


def set_known_group_ids(values: Iterable[object]) -> None:
    """刷新进程内的已确认群号缓存（`get_components()` 从快照读入后调用）。"""

    global _known_group_ids
    _known_group_ids = frozenset(
        group_id for group_id in (clean_str(value) for value in values) if group_id
    )


def confirm_group_entries(entries: Iterable[str], known_group_ids: Iterable[object]) -> list[str]:
    """白名单条目里，只保留「已知群号」集合内的条目（保持白名单顺序）。"""

    known = {group_id for group_id in (clean_str(value) for value in known_group_ids) if group_id}
    return [entry for entry in entries if entry in known]


def resolve_group_entries(entries: Sequence[str]) -> list[str]:
    """默认解析接缝：只确认进程内缓存里已知的群号。

    缓存由 `set_known_group_ids` 刷新（`get_components()` 读快照后调用）。缓存为空时
    一律不确认，组件层面 fail-closed。
    """

    return confirm_group_entries(entries, _known_group_ids)


def allowed_group_sessions(entries: Iterable[object]) -> list[str]:
    """把白名单条目投影成 `allowed_session` 的群号集合。

    三条约束：条目先过 `config_rules.normalize_stream_entries`，非法条目整份丢弃；
    只采信解析器确认过的条目；解析器返回白名单外的群号不采信。输出按白名单顺序去重。
    """

    normalized, rejection = normalize_stream_entries(entries)
    if rejection:
        return []

    confirmed = {
        group
        for group in (clean_str(value) for value in resolve_group_entries(normalized))
        if group
    }
    return [entry for entry in normalized if entry in confirmed]


# --------------------------------------------------------------------- 宿主查询胶水


async def _db_rows(ctx: object, filters: Mapping[str, object] | None) -> list[Mapping[str, Any]]:
    """查 `ChatSession` 行；能力缺失或调用失败一律按空清单（交由兜底路径）。"""

    db = getattr(ctx, "db", None)
    get = getattr(db, "get", None)
    if get is None:
        return []
    try:
        result = await get(
            CHAT_SESSION_MODEL,
            filters=dict(filters or {}),
            order_by=LAST_ACTIVE_FIELD,
        )
    except Exception:
        _LOGGER.warning("查询 ChatSession 失败，改用 chat 能力兜底", exc_info=True)
        return []
    return rows_of(result)


async def _chat_streams(ctx: object, method_name: str) -> list[Mapping[str, Any]]:
    """调 chat 能力的流清单方法（只扫内存活跃会话）；失败按空清单。"""

    chat = getattr(ctx, "chat", None)
    method = getattr(chat, method_name, None)
    if method is None:
        return []
    try:
        result = await method()
    except Exception:
        _LOGGER.debug("chat.%s 调用失败，按空清单处理", method_name, exc_info=True)
        return []
    return rows_of(result)


async def _chat_stream(ctx: object, method_name: str, **kwargs: object) -> Mapping[str, Any] | None:
    """调 chat 能力的单个流查找方法；返回 stream 结构或 None。"""

    chat = getattr(ctx, "chat", None)
    method = getattr(chat, method_name, None)
    if method is None:
        return None
    try:
        result = await method(**kwargs)
    except Exception:
        _LOGGER.debug("chat.%s 调用失败，按未找到处理", method_name, exc_info=True)
        return None
    return result if isinstance(result, Mapping) else None


def _is_group_row(row: object) -> bool:
    return bool(group_id_of(row))


async def fetch_group_sessions(ctx: object) -> list[Mapping[str, Any]]:
    """列出群会话行：DB 优先（不受活跃度限制），DB 为空时用 chat 活跃群兜底。"""

    rows = [row for row in await _db_rows(ctx, None) if _is_group_row(row)]
    if rows:
        return rows
    return [row for row in await _chat_streams(ctx, "get_group_streams") if _is_group_row(row)]


async def fetch_private_sessions(ctx: object) -> list[Mapping[str, Any]]:
    """列出私聊会话行，按最近活跃倒序；DB 为空时用 chat 活跃私聊兜底。"""

    rows = [
        row
        for row in await _db_rows(ctx, None)
        if not _is_group_row(row) and user_id_of(row)
    ]
    if not rows:
        rows = [
            row
            for row in await _chat_streams(ctx, "get_private_streams")
            if not _is_group_row(row) and user_id_of(row)
        ]
    return sorted(rows, key=_last_active_key, reverse=True)


async def fetch_known_group_ids(ctx: object) -> list[str]:
    """当前宿主已知的全部群号（用于刷新快照）。"""

    return group_ids_from_rows(await fetch_group_sessions(ctx))


@dataclass(frozen=True)
class ResolvedTarget:
    """解析结果：目标类型、人看的标识、内部 session_id 与显示名称。"""

    target_type: str
    """`group` 或 `private`。"""

    target_id: str
    """群号或 QQ 号。"""

    session_id: str
    """宿主内部的 32 位十六进制会话 ID。"""

    name: str
    """显示名称：群名或用户名称，取不到用号兜底。"""


async def resolve_target(
    ctx: object, *, target_type: str = "", target_id: str = ""
) -> ResolvedTarget | None:
    """把目标类型与号解析成会话目标；类型未知或查不到返回 None。

    `target_id` 是群号（`target_type` 为 `group`）或 QQ 号（`target_type` 为 `private`）。
    群聊路径要求候选行确实是群会话（group_id 命中）；私聊路径要求候选行不是群会话，
    避免群会话行里恰好相等的 user_id 被误当成私聊目标。
    """

    target = clean_str(target_id)

    if target_type == TARGET_TYPE_GROUP and target:
        rows = [
            row
            for row in await _db_rows(ctx, {"group_id": target})
            if group_id_of(row) == target
        ]
        row = pick_most_active_session(rows)
        if row is not None:
            return ResolvedTarget(
                TARGET_TYPE_GROUP, target, session_id_of(row), group_name_of(row) or target
            )
        stream = await _chat_stream(ctx, "get_stream_by_group_id", group_id=target)
        stream_id = session_id_of(stream)
        if stream_id:
            return ResolvedTarget(
                TARGET_TYPE_GROUP, target, stream_id, group_name_of(stream) or target
            )
        return None

    if target_type == TARGET_TYPE_PRIVATE and target:
        rows = [
            row
            for row in await _db_rows(ctx, {"user_id": target})
            if not _is_group_row(row) and user_id_of(row) == target
        ]
        row = pick_most_active_session(rows)
        if row is not None:
            return ResolvedTarget(
                TARGET_TYPE_PRIVATE, target, session_id_of(row), user_name_of(row) or target
            )
        stream = await _chat_stream(ctx, "get_stream_by_user_id", user_id=target)
        stream_id = session_id_of(stream)
        if stream_id:
            return ResolvedTarget(
                TARGET_TYPE_PRIVATE, target, stream_id, user_name_of(stream) or target
            )
        return None

    return None
