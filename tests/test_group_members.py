"""Участник класса без строки в users не должен ломать отчёты.

Регресс: /join добавлял человека в group_members, не создавая строку в users.
get_group_members брал user_id из users через LEFT JOIN, получал NULL, и любой
вызывающий код падал на int(None). На проде это выглядело так: нажимаешь на
класс в админке — не происходит вообще ничего.
"""
import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENV_KEYS = ("RUTUTOR_DB_PATH", "BOT_DB_PATH", "RUTUTOR_DATA_DIR",
            "RUTUTOR_LEGACY_DB_PATH", "BOT_LEGACY_DB_PATH")


def unload_storage():
    mod = sys.modules.pop("storage", None)
    if mod is not None:
        con = getattr(mod, "_con", None)
        if con is not None:
            con.close()


class GroupMembersTest(unittest.TestCase):
    def setUp(self):
        self.old_env = {k: os.environ.get(k) for k in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        unload_storage()
        self.tmp = tempfile.TemporaryDirectory()
        target = Path(self.tmp.name) / "bot.db"
        sqlite3.connect(str(target)).close()
        os.environ["RUTUTOR_DB_PATH"] = str(target)
        self.storage = importlib.import_module("storage")
        self.storage.init_db()
        self.group = self.storage.create_group("Тест")
        self.gid = int(self.group["group_id"])

    def tearDown(self):
        unload_storage()
        self.tmp.cleanup()
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_join_creates_the_user_row(self):
        self.storage.join_group_by_code(555, self.group["join_code"], "student")
        self.assertIsNotNone(self.storage.get_user(555), "/join обязан создать строку в users")

    def test_member_without_user_row_does_not_break_reports(self):
        """Старые «призраки», уже лежащие в базе, тоже не должны ронять отчёты."""
        con = self.storage._get_con()
        con.execute(
            "INSERT INTO group_members(group_id, user_id, role, joined_ts) VALUES (?,?,?,?)",
            (self.gid, 666, "student", 1),
        )
        con.commit()

        members = self.storage.get_group_members(self.gid)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["user_id"], 666, "user_id должен браться из group_members")
        self.assertEqual(members[0]["xp"], 0)
        self.assertEqual(members[0]["first_name"], "")

        # Ни один из этих вызовов не должен бросать TypeError.
        summary = self.storage.get_group_summary(self.gid)
        self.assertEqual(summary["students"], 1)
        self.assertEqual(summary["xp_avg"], 0)

        rows = self.storage.export_group_xp_rows(self.gid, ["s1_01"])
        self.assertEqual([r["user_id"] for r in rows], [666])

    def test_ensure_user_does_not_overwrite_existing_data(self):
        self.storage.upsert_user(1, "Аня", "anya")
        self.storage.add_xp(1, 50)
        self.storage.ensure_user(1)
        user = self.storage.get_user(1)
        self.assertEqual(user["first_name"], "Аня")
        self.assertEqual(user["xp"], 50)


if __name__ == "__main__":
    unittest.main()
