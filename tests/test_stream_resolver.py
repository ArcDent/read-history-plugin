"""`stream_resolver.py` 的解析测试（不依赖 maibot_sdk，本机可跑）。

任务 3.1 的两条接缝：

- 纯挑选函数 `pick_most_active_session`：多候选会话行取 `last_active_timestamp` 最新，
  空列表与缺字段行有确定行为（跳过没有 session_id 的行、没有时间的行视为最旧）。
- 完整解析链 `resolve_target`：优先 `database.get("ChatSession")`（查库不受活跃度限制），
  未命中再用 `chat.get_stream_by_group_id` / `get_stream_by_user_id` 兜底（只扫内存活跃
  会话），都空即未找到；同群多 session 取最近活跃者。

`confirm_group_entries` 是暴露面的群号确认子集（纯函数），期望值取自 spec
`chat-access-control` 场景：只回真正的群号，写错的 QQ 号撞上 group_id 也不放行。
"""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path

from support import (
    StubChat,
    StubContext,
    StubDatabase,
    load_plugin_module,
    session_row,
)

stream_resolver = load_plugin_module("stream_resolver")
target_params = load_plugin_module("target_params")

GROUP = "1919810"
OTHER_GROUP = "805457685"
PRIVATE_PEER = "10001"
SESSION_OF_GROUP = "eef21eda523391a356f8c60efcf1c493"
SESSION_OF_GROUP_OLD = "00112233445566778899aabbccddeeff"
SESSION_OF_PEER = "ffeeddccbbaa99887766554433221100"


class PickMostActiveSessionTests(unittest.TestCase):
    """纯挑选逻辑：从候选会话行挑最近活跃的一条。"""

    def test_newest_row_wins(self) -> None:
        rows = [
            session_row(SESSION_OF_GROUP_OLD, group_id=GROUP, last_active_timestamp="2026-10-01T10:00:00"),
            session_row(SESSION_OF_GROUP, group_id=GROUP, last_active_timestamp="2026-10-12T15:19:00"),
        ]
        picked = stream_resolver.pick_most_active_session(rows)
        self.assertEqual(stream_resolver.session_id_of(picked), SESSION_OF_GROUP)

    def test_input_order_does_not_matter(self) -> None:
        rows = [
            session_row(SESSION_OF_GROUP, group_id=GROUP, last_active_timestamp="2026-10-12T15:19:00"),
            session_row(SESSION_OF_GROUP_OLD, group_id=GROUP, last_active_timestamp="2026-10-01T10:00:00"),
        ]
        picked = stream_resolver.pick_most_active_session(rows)
        self.assertEqual(stream_resolver.session_id_of(picked), SESSION_OF_GROUP)

    def test_empty_list_returns_none(self) -> None:
        self.assertIsNone(stream_resolver.pick_most_active_session([]))

    def test_rows_without_session_id_are_skipped(self) -> None:
        rows = [{"group_id": GROUP, "last_active_timestamp": "2026-10-12T15:19:00"}]
        self.assertIsNone(stream_resolver.pick_most_active_session(rows))

    def test_row_without_timestamp_is_still_usable_but_oldest(self) -> None:
        rows = [
            session_row(SESSION_OF_GROUP, group_id=GROUP, last_active_timestamp=None),
            session_row(SESSION_OF_GROUP_OLD, group_id=GROUP, last_active_timestamp="2026-10-01T10:00:00"),
        ]
        picked = stream_resolver.pick_most_active_session(rows)
        self.assertEqual(stream_resolver.session_id_of(picked), SESSION_OF_GROUP_OLD)

    def test_only_row_without_timestamp_is_returned(self) -> None:
        rows = [session_row(SESSION_OF_GROUP, group_id=GROUP, last_active_timestamp=None)]
        picked = stream_resolver.pick_most_active_session(rows)
        self.assertEqual(stream_resolver.session_id_of(picked), SESSION_OF_GROUP)


class GroupIdsFromRowsTests(unittest.TestCase):
    """`group_ids_from_rows`：只保留群号非空的行。"""

    def test_only_group_rows_contribute(self) -> None:
        rows = [
            session_row(SESSION_OF_GROUP, group_id=GROUP, last_active_timestamp="2026-10-12T15:19:00"),
            session_row(SESSION_OF_PEER, user_id=PRIVATE_PEER, last_active_timestamp="2026-10-12T15:19:00"),
        ]
        self.assertEqual(stream_resolver.group_ids_from_rows(rows), [GROUP])

    def test_empty_rows_produce_empty_list(self) -> None:
        self.assertEqual(stream_resolver.group_ids_from_rows([]), [])


class ResolveTargetTests(unittest.IsolatedAsyncioTestCase):
    """完整解析链：DB 优先、chat 兜底、都空即未找到。"""

    async def test_group_resolves_via_database(self) -> None:
        ctx = StubContext(
            db=StubDatabase(
                [session_row(SESSION_OF_GROUP, group_id=GROUP, group_name="测试群",
                             last_active_timestamp="2026-10-12T15:19:00")]
            )
        )
        target = await stream_resolver.resolve_target(ctx, target_type=target_params.TARGET_TYPE_GROUP, target_id=GROUP)
        self.assertIsNotNone(target)
        self.assertEqual(target.session_id, SESSION_OF_GROUP)
        self.assertEqual(target.target_type, "group")
        self.assertEqual(target.name, "测试群")
        # 查库必须带 group_id 过滤且按活跃时间排序（不受活跃度限制）。
        self.assertEqual(ctx.db.calls[0]["model_name"], "ChatSession")
        self.assertEqual(ctx.db.calls[0]["filters"], {"group_id": GROUP})

    async def test_group_picks_newest_session(self) -> None:
        ctx = StubContext(
            db=StubDatabase(
                [
                    session_row(SESSION_OF_GROUP_OLD, group_id=GROUP, last_active_timestamp="2026-10-01T10:00:00"),
                    session_row(SESSION_OF_GROUP, group_id=GROUP, last_active_timestamp="2026-10-12T15:19:00"),
                ]
            )
        )
        target = await stream_resolver.resolve_target(ctx, target_type=target_params.TARGET_TYPE_GROUP, target_id=GROUP)
        self.assertEqual(target.session_id, SESSION_OF_GROUP)

    async def test_group_falls_back_to_chat_stream(self) -> None:
        ctx = StubContext(
            db=StubDatabase([]),
            chat=StubChat(
                stream_by_group={
                    GROUP: {"session_id": SESSION_OF_GROUP, "group_id": GROUP, "group_name": "活跃群"}
                }
            ),
        )
        target = await stream_resolver.resolve_target(ctx, target_type=target_params.TARGET_TYPE_GROUP, target_id=GROUP)
        self.assertIsNotNone(target)
        self.assertEqual(target.session_id, SESSION_OF_GROUP)
        self.assertEqual(target.name, "活跃群")
        self.assertIn(("get_stream_by_group_id", GROUP), ctx.chat.calls)

    async def test_group_not_found_when_both_empty(self) -> None:
        ctx = StubContext(db=StubDatabase([]), chat=StubChat())
        self.assertIsNone(await stream_resolver.resolve_target(ctx, target_type=target_params.TARGET_TYPE_GROUP, target_id=GROUP))

    async def test_group_name_falls_back_to_number(self) -> None:
        ctx = StubContext(
            db=StubDatabase(
                [session_row(SESSION_OF_GROUP, group_id=GROUP, last_active_timestamp="2026-10-12T15:19:00")]
            )
        )
        target = await stream_resolver.resolve_target(ctx, target_type=target_params.TARGET_TYPE_GROUP, target_id=GROUP)
        self.assertEqual(target.name, GROUP)

    async def test_private_resolves_via_database(self) -> None:
        ctx = StubContext(
            db=StubDatabase(
                [session_row(SESSION_OF_PEER, user_id=PRIVATE_PEER, user_nickname="小明",
                             last_active_timestamp="2026-10-12T15:19:00")]
            )
        )
        target = await stream_resolver.resolve_target(
            ctx, target_type=target_params.TARGET_TYPE_PRIVATE, target_id=PRIVATE_PEER
        )
        self.assertIsNotNone(target)
        self.assertEqual(target.target_type, "private")
        self.assertEqual(target.session_id, SESSION_OF_PEER)
        self.assertEqual(target.name, "小明")
        self.assertEqual(ctx.db.calls[0]["filters"], {"user_id": PRIVATE_PEER})

    async def test_private_ignores_group_session_rows(self) -> None:
        """带上 user_id 的群会话行不能被当成私聊目标。"""

        ctx = StubContext(
            db=StubDatabase(
                [session_row(SESSION_OF_GROUP, group_id=GROUP, user_id=PRIVATE_PEER,
                             last_active_timestamp="2026-10-12T15:19:00")]
            ),
            chat=StubChat(),
        )
        self.assertIsNone(await stream_resolver.resolve_target(
            ctx, target_type=target_params.TARGET_TYPE_PRIVATE, target_id=PRIVATE_PEER
        ))

    async def test_private_falls_back_to_chat_stream(self) -> None:
        ctx = StubContext(
            db=StubDatabase([]),
            chat=StubChat(
                stream_by_user={
                    PRIVATE_PEER: {"session_id": SESSION_OF_PEER, "user_id": PRIVATE_PEER,
                                   "user_nickname": "小明"}
                }
            ),
        )
        target = await stream_resolver.resolve_target(
            ctx, target_type=target_params.TARGET_TYPE_PRIVATE, target_id=PRIVATE_PEER
        )
        self.assertEqual(target.session_id, SESSION_OF_PEER)


class ConfirmGroupEntriesTests(unittest.TestCase):
    """暴露面的群号确认：只回真正的群号。"""

    def test_only_known_groups_are_confirmed(self) -> None:
        self.assertEqual(
            stream_resolver.confirm_group_entries([GROUP, PRIVATE_PEER], [GROUP]),
            [GROUP],
        )

    def test_unknown_entry_is_not_confirmed(self) -> None:
        self.assertEqual(stream_resolver.confirm_group_entries([PRIVATE_PEER], [GROUP]), [])

    def test_whitelist_order_is_preserved(self) -> None:
        self.assertEqual(
            stream_resolver.confirm_group_entries([OTHER_GROUP, GROUP], [GROUP, OTHER_GROUP]),
            [OTHER_GROUP, GROUP],
        )


class KnownGroupSnapshotTests(unittest.TestCase):
    """群号快照文件的读写：跨重载保留已确认的群号。"""

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "plugin"
            stream_resolver.write_known_group_ids(data_dir, [GROUP, OTHER_GROUP])
            self.assertEqual(
                stream_resolver.read_known_group_ids(data_dir),
                [GROUP, OTHER_GROUP],
            )

    def test_missing_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(stream_resolver.read_known_group_ids(Path(tmp) / "nope"), [])

    def test_corrupt_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / stream_resolver.KNOWN_GROUPS_FILENAME).write_text("not json", encoding="utf-8")
            self.assertEqual(stream_resolver.read_known_group_ids(data_dir), [])

    def test_corrupt_file_read_is_logged(self) -> None:
        """读失败不再静默：至少留一条 debug 日志，便于定位暴露面被摘掉的原因。"""

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / stream_resolver.KNOWN_GROUPS_FILENAME).write_text("not json", encoding="utf-8")
            with self.assertLogs(stream_resolver._LOGGER, level=logging.DEBUG) as captured:
                self.assertEqual(stream_resolver.read_known_group_ids(data_dir), [])
            self.assertTrue(any("快照" in line for line in captured.output), captured.output)

    def test_non_list_snapshot_is_logged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / stream_resolver.KNOWN_GROUPS_FILENAME).write_text(
                json.dumps({"group": GROUP}), encoding="utf-8"
            )
            with self.assertLogs(stream_resolver._LOGGER, level=logging.DEBUG):
                self.assertEqual(stream_resolver.read_known_group_ids(data_dir), [])

    def test_write_leaves_no_temp_file_behind(self) -> None:
        """原子写：先写临时文件再 os.replace，写完不残留临时文件。"""

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            stream_resolver.write_known_group_ids(data_dir, [GROUP])
            leftovers = [path.name for path in data_dir.glob("*.tmp")]
            self.assertEqual(leftovers, [])
            self.assertEqual(stream_resolver.read_known_group_ids(data_dir), [GROUP])

    def test_write_failure_does_not_raise(self) -> None:
        """写入目标不可用时只记日志，不向上抛（加载路径不能因此失败）。"""

        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("file, not directory", encoding="utf-8")
            with self.assertLogs(stream_resolver._LOGGER, level=logging.WARNING):
                stream_resolver.write_known_group_ids(blocker / "nested", [GROUP])

    def test_dropped_ids_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / stream_resolver.KNOWN_GROUPS_FILENAME).write_text(
                json.dumps([GROUP, 1919810, "", " 803 "]), encoding="utf-8"
            )
            self.assertEqual(stream_resolver.read_known_group_ids(data_dir), [GROUP, "803"])


class DefaultSeamTests(unittest.TestCase):
    """默认解析接缝消费群号快照：确认过的群号才进 allowed_session。"""

    def tearDown(self) -> None:
        stream_resolver.set_known_group_ids(())

    def test_unconfirmed_entries_stay_unexposed(self) -> None:
        stream_resolver.set_known_group_ids(())
        self.assertEqual(stream_resolver.resolve_group_entries([GROUP]), [])
        self.assertEqual(stream_resolver.allowed_group_sessions([GROUP]), [])

    def test_confirmed_group_enters_allowed_session(self) -> None:
        stream_resolver.set_known_group_ids([GROUP])
        self.assertEqual(stream_resolver.resolve_group_entries([GROUP, PRIVATE_PEER]), [GROUP])
        self.assertEqual(stream_resolver.allowed_group_sessions([GROUP, PRIVATE_PEER]), [GROUP])

    def test_resolver_output_outside_whitelist_is_dropped(self) -> None:
        stream_resolver.set_known_group_ids([OTHER_GROUP])
        self.assertEqual(stream_resolver.allowed_group_sessions([GROUP]), [])

    def test_entries_are_normalized_before_confirmation(self) -> None:
        stream_resolver.set_known_group_ids([GROUP])
        self.assertEqual(stream_resolver.allowed_group_sessions([" 1919810 ", "1919810"]), [GROUP])

    def test_invalid_entry_drops_whole_whitelist(self) -> None:
        stream_resolver.set_known_group_ids([GROUP])
        self.assertEqual(stream_resolver.allowed_group_sessions([GROUP, "abc"]), [])


if __name__ == "__main__":
    unittest.main()
