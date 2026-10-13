"""任务 4.1：`summary_store.py` 的自建摘要存储测试（不依赖 maibot_sdk，本机可跑）。

接缝是存储模块导出的函数：SQL 逻辑用临时目录里的 sqlite 文件驱动，断言表结构、
写入、按 target_key 读取、新鲜度判定与覆盖更新语义。表结构取自 design.md 的 D5
（`summaries(target_key 主键, target_type, cutoff_ts, text, created_ts)`），
新鲜度语义取自 summary-generation spec 的场景字面量。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from support import load_plugin_module

summary_store = load_plugin_module("summary_store")

GROUP = "1919810"
PRIVATE_PEER = "10001"


def table_info(db_path: Path) -> list[tuple]:
    """读 sqlite 表的 PRAGMA table_info 结果。"""

    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(f"PRAGMA table_info({summary_store.TABLE_NAME})").fetchall()
    finally:
        connection.close()


def column_names(db_path: Path) -> list[str]:
    """读 sqlite 表的列名清单。"""

    return [row[1] for row in table_info(db_path)]


class SummaryDbPathTests(unittest.TestCase):
    """摘要库文件落在插件 data_dir 下，拿不到 data_dir 时不落盘。"""

    def test_path_is_inside_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "plugins" / "read_history_plugin"
            path = summary_store.summary_db_path(data_dir)
            self.assertEqual(path, data_dir / summary_store.SUMMARY_DB_FILENAME)

    def test_string_data_dir_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = summary_store.summary_db_path(tmp)
            self.assertEqual(path, Path(tmp) / summary_store.SUMMARY_DB_FILENAME)

    def test_missing_data_dir_gives_none(self) -> None:
        self.assertIsNone(summary_store.summary_db_path(None))
        self.assertIsNone(summary_store.summary_db_path(""))


class TargetKeyTests(unittest.TestCase):
    """`build_target_key` 的键格式：`类型:号`。"""

    def test_group_key(self) -> None:
        self.assertEqual(summary_store.build_target_key("group", GROUP), f"group:{GROUP}")

    def test_private_key(self) -> None:
        self.assertEqual(
            summary_store.build_target_key("private", PRIVATE_PEER), f"private:{PRIVATE_PEER}"
        )


class SchemaTests(unittest.TestCase):
    """表结构：五列，target_key 是主键。"""

    def test_table_has_exactly_the_designed_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / summary_store.SUMMARY_DB_FILENAME
            summary_store.save_summary(
                db_path,
                target_key=f"group:{GROUP}",
                target_type="group",
                text="摘要",
                cutoff_ts=1000.0,
                created_ts=1000.0,
            )
            self.assertEqual(
                set(column_names(db_path)),
                {"target_key", "target_type", "cutoff_ts", "text", "created_ts"},
            )

    def test_target_key_is_the_primary_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / summary_store.SUMMARY_DB_FILENAME
            summary_store.save_summary(
                db_path,
                target_key=f"group:{GROUP}",
                target_type="group",
                text="摘要",
                cutoff_ts=1000.0,
                created_ts=1000.0,
            )
            primary_keys = {row[1]: row[5] for row in table_info(db_path)}
            self.assertEqual(primary_keys["target_key"], 1)
            self.assertEqual(
                [name for name, order in primary_keys.items() if order], ["target_key"]
            )


class RoundTripTests(unittest.TestCase):
    """写入与按 target_key 读取。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / summary_store.SUMMARY_DB_FILENAME

    def test_saved_summary_is_read_back(self) -> None:
        summary_store.save_summary(
            self.db_path,
            target_key=f"group:{GROUP}",
            target_type="group",
            text="这段时间在聊天气",
            cutoff_ts=1786000000.0,
            created_ts=1786000010.0,
        )
        record = summary_store.read_summary(self.db_path, f"group:{GROUP}")
        self.assertIsNotNone(record)
        self.assertEqual(record.target_key, f"group:{GROUP}")
        self.assertEqual(record.target_type, "group")
        self.assertEqual(record.text, "这段时间在聊天气")
        self.assertEqual(record.cutoff_ts, 1786000000.0)
        self.assertEqual(record.created_ts, 1786000010.0)

    def test_missing_key_reads_none(self) -> None:
        self.assertIsNone(summary_store.read_summary(self.db_path, f"group:{GROUP}"))

    def test_cutoff_is_independent_from_created(self) -> None:
        """cutoff 是覆盖截止时刻，created 是生成时刻，两者分开存且各自等于写入值。"""

        summary_store.save_summary(
            self.db_path,
            target_key=f"group:{GROUP}",
            target_type="group",
            text="增量摘要",
            cutoff_ts=1786000300.0,
            created_ts=1786000400.0,
        )
        record = summary_store.read_summary(self.db_path, f"group:{GROUP}")
        self.assertEqual(record.cutoff_ts, 1786000300.0)
        self.assertEqual(record.created_ts, 1786000400.0)


class OverwriteTests(unittest.TestCase):
    """覆盖更新语义：同一 target_key 重复写以后写为准，且只保留一行。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / summary_store.SUMMARY_DB_FILENAME

    def test_second_write_wins(self) -> None:
        key = f"group:{GROUP}"
        summary_store.save_summary(
            self.db_path, target_key=key, target_type="group",
            text="旧摘要", cutoff_ts=1000.0, created_ts=1000.0,
        )
        summary_store.save_summary(
            self.db_path, target_key=key, target_type="group",
            text="新摘要", cutoff_ts=2000.0, created_ts=2000.0,
        )
        record = summary_store.read_summary(self.db_path, key)
        self.assertEqual(record.text, "新摘要")
        self.assertEqual(record.created_ts, 2000.0)

    def test_overwrite_keeps_a_single_row(self) -> None:
        key = f"group:{GROUP}"
        for index in range(3):
            summary_store.save_summary(
                self.db_path, target_key=key, target_type="group",
                text=f"第{index}版", cutoff_ts=float(index), created_ts=float(index),
            )
        connection = sqlite3.connect(self.db_path)
        try:
            count = connection.execute(
                f"SELECT COUNT(*) FROM {summary_store.TABLE_NAME}"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 1)

    def test_different_keys_coexist(self) -> None:
        summary_store.save_summary(
            self.db_path, target_key=f"group:{GROUP}", target_type="group",
            text="群摘要", cutoff_ts=1000.0, created_ts=1000.0,
        )
        summary_store.save_summary(
            self.db_path, target_key=f"private:{PRIVATE_PEER}", target_type="private",
            text="私聊摘要", cutoff_ts=2000.0, created_ts=2000.0,
        )
        self.assertEqual(
            summary_store.read_summary(self.db_path, f"group:{GROUP}").text, "群摘要"
        )
        self.assertEqual(
            summary_store.read_summary(self.db_path, f"private:{PRIVATE_PEER}").text, "私聊摘要"
        )


class FreshnessTests(unittest.TestCase):
    """新鲜度判定：创建时间在窗口内即新鲜。"""

    def _record(self, created_ts: float):
        return summary_store.SummaryRecord(
            target_key=f"group:{GROUP}",
            target_type="group",
            cutoff_ts=created_ts,
            text="摘要",
            created_ts=created_ts,
        )

    def test_created_just_now_is_fresh(self) -> None:
        self.assertTrue(summary_store.is_fresh(self._record(999.0), 3600, 1000.0))

    def test_at_window_boundary_is_fresh(self) -> None:
        self.assertTrue(summary_store.is_fresh(self._record(1000.0 - 3600), 3600, 1000.0))

    def test_beyond_window_is_stale(self) -> None:
        self.assertFalse(summary_store.is_fresh(self._record(1000.0 - 3601), 3600, 1000.0))

    def test_zero_window_only_accepts_now(self) -> None:
        self.assertTrue(summary_store.is_fresh(self._record(1000.0), 0, 1000.0))
        self.assertFalse(summary_store.is_fresh(self._record(999.0), 0, 1000.0))


class LoadFreshTests(unittest.TestCase):
    """`load_fresh_summary`：窗口内返回记录，过期或缺失返回 None。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / summary_store.SUMMARY_DB_FILENAME
        self.key = f"group:{GROUP}"

    def _save(self, *, text: str, created_ts: float) -> None:
        summary_store.save_summary(
            self.db_path, target_key=self.key, target_type="group",
            text=text, cutoff_ts=created_ts, created_ts=created_ts,
        )

    def test_fresh_record_is_returned(self) -> None:
        self._save(text="新鲜摘要", created_ts=1000.0)
        record = summary_store.load_fresh_summary(self.db_path, self.key, 3600, 1100.0)
        self.assertIsNotNone(record)
        self.assertEqual(record.text, "新鲜摘要")

    def test_stale_record_is_not_returned(self) -> None:
        self._save(text="过期摘要", created_ts=1000.0)
        self.assertIsNone(summary_store.load_fresh_summary(self.db_path, self.key, 3600, 9000.0))

    def test_missing_key_is_none(self) -> None:
        self.assertIsNone(summary_store.load_fresh_summary(self.db_path, self.key, 3600, 1000.0))

    def test_missing_data_dir_is_none(self) -> None:
        self.assertIsNone(summary_store.load_fresh_summary(None, self.key, 3600, 1000.0))


class StorageLocationTests(unittest.TestCase):
    """写入在 data_dir 下建库建表，不依赖宿主数据库。"""

    def test_save_creates_parent_directory_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "nested" / "data_dir"
            db_path = summary_store.summary_db_path(data_dir)
            summary_store.save_summary(
                db_path, target_key=f"group:{GROUP}", target_type="group",
                text="摘要", cutoff_ts=1.0, created_ts=1.0,
            )
            self.assertTrue(db_path.is_file())
            self.assertEqual(db_path.parent, data_dir)

    def test_save_without_path_reports_failure(self) -> None:
        self.assertFalse(
            summary_store.save_summary(
                None, target_key=f"group:{GROUP}", target_type="group",
                text="摘要", cutoff_ts=1.0, created_ts=1.0,
            )
        )

    def test_save_with_path_reports_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / summary_store.SUMMARY_DB_FILENAME
            self.assertTrue(
                summary_store.save_summary(
                    db_path, target_key=f"group:{GROUP}", target_type="group",
                    text="摘要", cutoff_ts=1.0, created_ts=1.0,
                )
            )


if __name__ == "__main__":
    unittest.main()
