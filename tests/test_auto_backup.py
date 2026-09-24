"""Еженедельная автокопия базы: расписание и сама отправка."""
import importlib
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENV_KEYS = ("RUTUTOR_DB_PATH", "BOT_DB_PATH", "RUTUTOR_DATA_DIR",
            "RUTUTOR_LEGACY_DB_PATH", "BOT_LEGACY_DB_PATH",
            "RUTUTOR_BACKUP_CHAT_ID", "RUTUTOR_ALERT_CHAT_ID",
            "RUTUTOR_BACKUP_WEEKDAY", "RUTUTOR_BACKUP_HOUR",
            "RUTUTOR_BACKUP_TZ_OFFSET")

SATURDAY = datetime(2026, 9, 26, 3, 0)   # суббота, 03:00
FRIDAY = datetime(2026, 9, 25, 3, 0)


def unload(*names):
    for name in names:
        mod = sys.modules.pop(name, None)
        if name == "storage" and mod is not None:
            con = getattr(mod, "_con", None)
            if con is not None:
                con.close()


class FakeBot:
    def __init__(self, fail=False):
        self.docs = []
        self.fail = fail

    def send_document(self, chat_id, f, caption=""):
        if self.fail:
            raise RuntimeError("chat not found")
        self.docs.append((chat_id, f.name, len(f.read()), caption))


class AutoBackupTest(unittest.TestCase):
    def setUp(self):
        self.old_env = {k: os.environ.get(k) for k in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        unload("storage", "auto_backup")
        self.tmp = tempfile.TemporaryDirectory()
        target = Path(self.tmp.name) / "bot.db"
        sqlite3.connect(str(target)).close()
        os.environ["RUTUTOR_DB_PATH"] = str(target)
        self.storage = importlib.import_module("storage")
        self.storage.init_db()
        self.storage.upsert_user(1, "Аня", "anya")
        self.backup = importlib.import_module("auto_backup")

    def tearDown(self):
        unload("storage", "auto_backup")
        self.tmp.cleanup()
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_runs_on_saturday_only_once(self):
        self.assertTrue(self.backup.should_run(SATURDAY, ""))
        self.assertFalse(self.backup.should_run(SATURDAY, "2026-09-26"),
                         "в один день копия должна делаться один раз")
        self.assertFalse(self.backup.should_run(FRIDAY, ""), "в пятницу копий быть не должно")

    def test_waits_for_the_configured_hour(self):
        early = SATURDAY.replace(hour=1)
        self.assertFalse(self.backup.should_run(early, ""))
        self.assertTrue(self.backup.should_run(SATURDAY.replace(hour=23), ""))

    def test_weekday_and_hour_are_configurable(self):
        os.environ["RUTUTOR_BACKUP_WEEKDAY"] = "0"   # понедельник
        os.environ["RUTUTOR_BACKUP_HOUR"] = "9"
        monday = datetime(2026, 9, 28, 9, 0)
        self.assertTrue(self.backup.should_run(monday, ""))
        self.assertFalse(self.backup.should_run(SATURDAY, ""))

    def test_make_backup_sends_file_and_marks_the_day(self):
        bot = FakeBot()
        self.assertTrue(self.backup.make_backup(bot, 777, SATURDAY))
        self.assertEqual(len(bot.docs), 1)
        chat_id, name, size, caption = bot.docs[0]
        self.assertEqual(chat_id, 777)
        self.assertIn("2026-09-26", name)
        self.assertGreater(size, 0)
        self.assertIn("Учеников: 1", caption)
        self.assertEqual(self.storage.get_meta(self.backup.LAST_BACKUP_KEY), "2026-09-26")
        self.assertFalse(self.backup.should_run(SATURDAY, "2026-09-26"))

    def test_failed_send_is_not_marked_as_done(self):
        """Если отправка сорвалась, копию надо попробовать снова, а не считать сделанной."""
        bot = FakeBot(fail=True)
        self.assertFalse(self.backup.make_backup(bot, 777, SATURDAY))
        self.assertEqual(self.storage.get_meta(self.backup.LAST_BACKUP_KEY, ""), "")
        self.assertTrue(self.backup.should_run(SATURDAY, ""))

    def test_disabled_without_a_chat_id(self):
        self.assertIsNone(self.backup.backup_chat_id())
        self.assertFalse(self.backup.start(FakeBot()))

    def test_falls_back_to_alert_chat(self):
        os.environ["RUTUTOR_ALERT_CHAT_ID"] = "555"
        self.assertEqual(self.backup.backup_chat_id(), 555)
        os.environ["RUTUTOR_BACKUP_CHAT_ID"] = "777"
        self.assertEqual(self.backup.backup_chat_id(), 777, "явная переменная важнее")

    def test_start_launches_a_daemon_thread(self):
        os.environ["RUTUTOR_BACKUP_CHAT_ID"] = "777"
        self.assertTrue(self.backup.start(FakeBot()))


if __name__ == "__main__":
    unittest.main()
