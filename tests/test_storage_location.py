"""Где бот хранит базу и умеет ли он понять, что диск временный.

Главный сценарий: на Railway без подключённого Volume файловая система
контейнера пересоздаётся при каждом деплое — ученики, XP и классы пропадают.
"""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENV_KEYS = (
    "RUTUTOR_DB_PATH", "BOT_DB_PATH", "RUTUTOR_DATA_DIR",
    "RUTUTOR_LEGACY_DB_PATH", "BOT_LEGACY_DB_PATH",
    "RAILWAY_VOLUME_MOUNT_PATH", "RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID",
    "RUTUTOR_ASSUME_CONTAINER",
)


def unload_storage():
    mod = sys.modules.pop("storage", None)
    if mod is not None:
        con = getattr(mod, "_con", None)
        if con is not None:
            con.close()


class StorageLocationTest(unittest.TestCase):
    def setUp(self):
        self.old_env = {k: os.environ.get(k) for k in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        unload_storage()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        unload_storage()
        self.tmp.cleanup()
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _import_storage(self):
        return importlib.import_module("storage")

    def test_uses_railway_volume_when_mounted(self):
        os.environ["RUTUTOR_ASSUME_CONTAINER"] = "1"
        os.environ["RAILWAY_VOLUME_MOUNT_PATH"] = self.tmp.name
        storage = self._import_storage()
        self.assertEqual(
            Path(storage.get_db_path()),
            Path(self.tmp.name) / "rututor" / "bot.db",
        )
        self.assertTrue(storage.is_persistent_storage())

    def test_flags_ephemeral_disk_when_no_volume(self):
        os.environ["RUTUTOR_ASSUME_CONTAINER"] = "1"
        storage = self._import_storage()
        self.assertFalse(
            storage.is_persistent_storage(),
            "без Volume хранилище обязано определяться как временное",
        )
        health = storage.storage_health()
        self.assertFalse(health["persistent"])
        self.assertTrue(health["in_container"])

    def test_explicit_path_is_trusted(self):
        os.environ["RUTUTOR_ASSUME_CONTAINER"] = "1"
        os.environ["RUTUTOR_DATA_DIR"] = self.tmp.name
        storage = self._import_storage()
        self.assertEqual(Path(storage.get_db_path()), Path(self.tmp.name) / "bot.db")
        self.assertTrue(storage.is_persistent_storage())

    def test_local_run_is_always_persistent(self):
        storage = self._import_storage()
        self.assertTrue(storage.is_persistent_storage())

    def test_backup_copies_real_data(self):
        target = Path(self.tmp.name) / "bot.db"
        os.environ["RUTUTOR_DB_PATH"] = str(target)
        import sqlite3
        sqlite3.connect(str(target)).close()
        storage = self._import_storage()
        storage.init_db()
        storage.upsert_user(1, "Аня", "anya")
        storage.add_xp(1, 42)

        backup = Path(self.tmp.name) / "copy.db"
        self.assertTrue(storage.backup_db_to(str(backup)))
        self.assertTrue(backup.exists())

        con = sqlite3.connect(str(backup))
        xp = con.execute("SELECT xp FROM users WHERE user_id=1").fetchone()[0]
        con.close()
        self.assertEqual(xp, 42)


if __name__ == "__main__":
    unittest.main()
