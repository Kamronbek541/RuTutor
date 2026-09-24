"""Уборка технических сообщений /cleanup_alerts.

Главное требование: команда удаляет ТОЛЬКО сообщения бота с предупреждением
о временном диске. Сообщения людей и остальные сообщения бота не трогаются.
"""
import importlib
import os
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WARN = "⚠️ База на временном диске\n\nПуть: /root/.rututor/bot.db"

ENV_KEYS = ("RUTUTOR_DB_PATH", "BOT_DB_PATH", "RUTUTOR_DATA_DIR",
            "RUTUTOR_LEGACY_DB_PATH", "BOT_LEGACY_DB_PATH", "ADMIN_IDS")


def unload(*names):
    for name in names:
        mod = sys.modules.pop(name, None)
        if name == "storage" and mod is not None:
            con = getattr(mod, "_con", None)
            if con is not None:
                con.close()


class FakeBot:
    """Минимальный двойник telebot: запоминает, что удалено и переслано."""

    def __init__(self, chats, next_ids):
        self.handlers = {}
        self.chats = chats
        self.next_id = dict(next_ids)
        self.deleted = {chat: [] for chat in chats}
        self.sent = []

    def message_handler(self, *a, **k):
        def deco(fn):
            self.handlers[k.get("commands", [fn.__name__])[0]] = fn
            return fn
        return deco

    def callback_query_handler(self, *a, **k):
        return lambda fn: fn

    def _new(self, chat):
        mid = self.next_id.get(chat, 500)
        self.next_id[chat] = mid + 1
        return mid

    def reply_to(self, msg, text, **k):
        return types.SimpleNamespace(message_id=self._new(msg.chat.id))

    def send_message(self, chat, text, **k):
        self.sent.append((chat, text))
        return types.SimpleNamespace(message_id=self._new(chat))

    def delete_message(self, chat, mid):
        self.deleted.setdefault(chat, []).append(mid)
        if chat in self.chats and mid in self.chats[chat]:
            del self.chats[chat][mid]

    def forward_message(self, to_chat, from_chat, mid):
        text = self.chats.get(from_chat, {}).get(mid, "__missing__")
        if text == "__missing__":
            raise RuntimeError("message not found")
        if text is None:
            raise RuntimeError("cannot forward this message")  # сообщение человека
        return types.SimpleNamespace(message_id=self._new(to_chat), text=text, caption=None)

    def send_document(self, *a, **k):
        pass


class CleanupAlertsTest(unittest.TestCase):
    def setUp(self):
        self.old_env = {k: os.environ.get(k) for k in ENV_KEYS}
        self.old_sleep = time.sleep
        time.sleep = lambda *_: None
        self.tmp = tempfile.TemporaryDirectory()
        target = Path(self.tmp.name) / "bot.db"
        sqlite3.connect(str(target)).close()
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["RUTUTOR_DB_PATH"] = str(target)
        os.environ["ADMIN_IDS"] = "777,888"
        unload("storage", "admin")
        self.storage = importlib.import_module("storage")
        self.storage.init_db()
        self.admin = importlib.import_module("admin")

    def tearDown(self):
        time.sleep = self.old_sleep
        unload("storage", "admin")
        self.tmp.cleanup()
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _run_cleanup(self, chats, next_ids, caller=777):
        fake = FakeBot(chats, next_ids)
        self.admin.register_prewarm_command(fake)
        msg = types.SimpleNamespace(
            chat=types.SimpleNamespace(id=999),
            from_user=types.SimpleNamespace(id=caller),
            message_id=1,
            text="/cleanup_alerts",
        )
        fake.handlers["cleanup_alerts"](msg)
        return fake

    def test_removes_only_the_warning_messages(self):
        chats = {
            777: {101: "Привет", 102: WARN, 103: WARN, 104: "Выбирай действие:", 105: WARN},
            888: {201: WARN, 202: "Мой профиль", 203: None},  # 203 — сообщение человека
        }
        fake = self._run_cleanup(chats, {777: 106, 888: 204, 999: 900})

        self.assertEqual(chats[777], {101: "Привет", 104: "Выбирай действие:"})
        self.assertEqual(chats[888], {202: "Мой профиль", 203: None})

        summary = fake.sent[-1][1]
        self.assertIn("Всего удалено: <b>4</b>", summary)

    def test_does_nothing_when_there_are_no_warnings(self):
        chats = {777: {101: "Привет", 102: "Меню"}, 888: {201: "Профиль"}}
        fake = self._run_cleanup(chats, {777: 103, 888: 202, 999: 900})
        self.assertEqual(chats[777], {101: "Привет", 102: "Меню"})
        self.assertEqual(chats[888], {201: "Профиль"})
        self.assertIn("Всего удалено: <b>0</b>", fake.sent[-1][1])

    def test_rejects_non_admin(self):
        chats = {777: {101: WARN}, 888: {}}
        fake = FakeBot(chats, {777: 102, 888: 200, 999: 900})
        self.admin.register_prewarm_command(fake)
        msg = types.SimpleNamespace(
            chat=types.SimpleNamespace(id=999),
            from_user=types.SimpleNamespace(id=123456),  # не админ
            message_id=1, text="/cleanup_alerts",
        )
        fake.handlers["cleanup_alerts"](msg)
        self.assertEqual(chats[777], {101: WARN}, "чужие сообщения не должны трогаться")

    def test_survives_unavailable_chat(self):
        """Если пользователь заблокировал бота, команда не должна падать."""
        chats = {777: {101: WARN}, 888: {}}
        fake = FakeBot(chats, {777: 102, 888: 200, 999: 900})
        original_send = fake.send_message

        def send(chat, text, **k):
            if chat == 888:
                raise RuntimeError("bot was blocked by the user")
            return original_send(chat, text, **k)

        fake.send_message = send
        self.admin.register_prewarm_command(fake)
        msg = types.SimpleNamespace(
            chat=types.SimpleNamespace(id=999),
            from_user=types.SimpleNamespace(id=777),
            message_id=1, text="/cleanup_alerts",
        )
        fake.handlers["cleanup_alerts"](msg)
        self.assertEqual(chats[777], {})
        self.assertIn("чат недоступен", fake.sent[-1][1])


if __name__ == "__main__":
    unittest.main()
