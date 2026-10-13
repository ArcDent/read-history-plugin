"""中期摘要的自建 sqlite 存储（纯逻辑，不 import maibot_sdk，本机可单测）。

为什么自建而不是用 `ctx.database.save`：宿主数据库按模型名映射宿主模型，插件私有
数据没有对应的宿主模型，写不进去（design D5）。所以摘要落在插件自己的 `data_dir`
下的一张 sqlite 表里，与宿主数据库完全隔离。

一张表，五个字段，与 design D5 逐字段一致：

```
summaries(target_key TEXT PRIMARY KEY, target_type TEXT, cutoff_ts REAL, text TEXT, created_ts REAL)
```

- `target_key` 是「类型:号」（如 `group:1919810`），跨群唯一；
- `target_type` 是 `group` 或 `private`，供读取时还原目标语义；
- `cutoff_ts` 是这条摘要覆盖到的时刻（下次概括从此刻起取增量）；
- `text` 是概括正文；
- `created_ts` 是生成时刻，新鲜度判定只看它。

并发从简：同目标重复生成允许，后写覆盖（`INSERT OR REPLACE`），不会出现两行。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .coercion import as_float, as_path, clean_str, path_in_dir

SUMMARY_DB_FILENAME: Final[str] = "summaries.sqlite3"
"""摘要库文件名，落在插件 `data_dir` 下。"""

TABLE_NAME: Final[str] = "summaries"
"""摘要表名。"""

_CREATE_TABLE_SQL: Final[str] = (
    f"CREATE TABLE IF NOT EXISTS {TABLE_NAME} ("
    "target_key TEXT PRIMARY KEY, "
    "target_type TEXT NOT NULL, "
    "cutoff_ts REAL NOT NULL, "
    "text TEXT NOT NULL, "
    "created_ts REAL NOT NULL"
    ")"
)

_UPSERT_SQL: Final[str] = (
    f"INSERT OR REPLACE INTO {TABLE_NAME} "
    "(target_key, target_type, cutoff_ts, text, created_ts) VALUES (?, ?, ?, ?, ?)"
)

_SELECT_SQL: Final[str] = (
    f"SELECT target_key, target_type, cutoff_ts, text, created_ts "
    f"FROM {TABLE_NAME} WHERE target_key = ?"
)


@dataclass(frozen=True)
class SummaryRecord:
    """一条摘要记录，字段与表列一一对应。"""

    target_key: str
    target_type: str
    cutoff_ts: float
    text: str
    created_ts: float


def build_target_key(target_type: object, target_id: object) -> str:
    """把目标类型与号拼成主键：`group:1919810` / `private:10001`。"""

    return f"{clean_str(target_type)}:{clean_str(target_id)}"


def summary_db_path(data_dir: object) -> Path | None:
    """把插件 data_dir 规整成摘要库文件路径；拿不到目录返回 None。"""

    return path_in_dir(data_dir, SUMMARY_DB_FILENAME)


def is_fresh(record: SummaryRecord, window_seconds: object, now: object) -> bool:
    """摘要在新鲜度窗口内为真：`now - created_ts <= window_seconds`。

    边界值（正好等于窗口）算新鲜。`created_ts` 晚于 `now`（时钟回拨）同样算新鲜，
    避免把刚写的摘要判成过期而反复重生成。窗口或时刻取不到数值时按过期处理。
    """

    window = as_float(window_seconds)
    current = as_float(now)
    if window is None or current is None:
        return False
    return current - float(record.created_ts) <= max(0.0, window)


def save_summary(
    db_path: object,
    *,
    target_key: str,
    target_type: str,
    text: str,
    cutoff_ts: object,
    created_ts: object,
) -> bool:
    """写入或覆盖一条摘要；成功返回 True。

    同一 `target_key` 重复写以后写为准（`INSERT OR REPLACE`）。库文件不存在时连同
    父目录一起创建。`db_path` 拿不到（插件没有 data_dir）或写入失败返回 False，
    调用方据此告知「本次无法生成」，不会把摘要当作已存储。
    """

    path = as_path(db_path)
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        try:
            connection.execute(_CREATE_TABLE_SQL)
            connection.execute(
                _UPSERT_SQL,
                (
                    clean_str(target_key),
                    clean_str(target_type),
                    float(as_float(cutoff_ts) or 0.0),
                    str(text),
                    float(as_float(created_ts) or 0.0),
                ),
            )
            connection.commit()
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return False
    return True


def read_summary(db_path: object, target_key: object) -> SummaryRecord | None:
    """按 target_key 读一条摘要；文件缺失、表缺失或键不存在都返回 None。"""

    path = as_path(db_path)
    if path is None or not path.is_file():
        return None
    try:
        connection = sqlite3.connect(path)
        try:
            row = connection.execute(_SELECT_SQL, (clean_str(target_key),)).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return _row_to_record(row)


def load_fresh_summary(
    db_path: object,
    target_key: object,
    window_seconds: object,
    now: object,
) -> SummaryRecord | None:
    """读 target_key 的摘要，仅在新鲜度窗口内才返回；否则 None。"""

    record = read_summary(db_path, target_key)
    if record is None or not is_fresh(record, window_seconds, now):
        return None
    return record


def _row_to_record(row: Iterable[object] | None) -> SummaryRecord | None:
    """把 sqlite 行转成记录；行形态不对返回 None。"""

    if row is None:
        return None
    values = list(row)
    if len(values) != 5:
        return None
    target_key, target_type, cutoff_ts, text, created_ts = values
    cutoff = as_float(cutoff_ts)
    created = as_float(created_ts)
    if cutoff is None or created is None or not isinstance(text, str):
        return None
    return SummaryRecord(
        target_key=str(target_key),
        target_type=str(target_type),
        cutoff_ts=cutoff,
        text=text,
        created_ts=created,
    )
