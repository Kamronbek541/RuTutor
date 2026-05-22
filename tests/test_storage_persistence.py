import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


def unload_storage():
    mod = sys.modules.pop("storage", None)
    if mod is not None:
        con = getattr(mod, "_con", None)
        if con is not None:
            con.close()


class StoragePersistenceTest(unittest.TestCase):
    def setUp(self):
        self.old_env = {k: os.environ.get(k) for k in ("RUTUTOR_DB_PATH", "BOT_DB_PATH", "RUTUTOR_DATA_DIR", "RUTUTOR_LEGACY_DB_PATH", "BOT_LEGACY_DB_PATH")}
        unload_storage()

    def tearDown(self):
        unload_storage()
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_migrates_legacy_db_to_stable_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            legacy = tmp_path / "old" / "bot.db"
            target = tmp_path / "stable" / "bot.db"
            legacy.parent.mkdir()

            con = sqlite3.connect(str(legacy))
            con.execute("""
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT,
                    username TEXT,
                    created_ts INTEGER,
                    language_level TEXT DEFAULT 'NA',
                    xp INTEGER DEFAULT 0,
                    streak INTEGER DEFAULT 0,
                    last_active_ymd TEXT DEFAULT '',
                    mode TEXT DEFAULT 'home'
                )
            """)
            con.execute("INSERT INTO users(user_id, first_name, username, created_ts, xp) VALUES (?,?,?,?,?)", (1, "Past Leader", "past", 1, 777))
            con.execute("""
                CREATE TABLE ktp_lesson_cache (
                    lesson_id TEXT PRIMARY KEY,
                    package_json TEXT,
                    created_ts INTEGER
                )
            """)
            con.execute(
                "INSERT INTO ktp_lesson_cache(lesson_id, package_json, created_ts) VALUES (?,?,?)",
                ("s1_01", '{"practice":[{"q":"saved"}],"exam":[]}', 1),
            )
            con.commit()
            con.close()

            os.environ["RUTUTOR_DB_PATH"] = str(target)
            os.environ["RUTUTOR_LEGACY_DB_PATH"] = str(legacy)

            storage = importlib.import_module("storage")
            storage.init_db()

            self.assertEqual(Path(storage.get_db_path()), target)
            self.assertTrue(target.exists())
            self.assertEqual(storage.get_top_users(1)[0]["xp"], 777)
            self.assertEqual(storage.get_ktp_cache("s1_01")["practice"][0]["q"], "saved")
            unload_storage()


if __name__ == "__main__":
    unittest.main()
