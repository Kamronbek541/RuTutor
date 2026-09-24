# auto_backup.py — еженедельная автоматическая резервная копия базы.
#
# Работает внутри самого бота, без cron и внешних сервисов: фоновый поток
# просыпается раз в несколько минут и, если наступил нужный день недели и час,
# отправляет файл базы в заданный чат. Отметка о последней копии лежит в базе,
# поэтому перезапуск процесса не приводит к повторной отправке.
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import storage

LAST_BACKUP_KEY = "last_auto_backup_ymd"
CHECK_INTERVAL_SEC = 600  # как часто просыпаться и смотреть на часы

WEEKDAY_NAMES = ["понедельник", "вторник", "среду", "четверг",
                 "пятницу", "субботу", "воскресенье"]

log = logging.getLogger("auto_backup")


def _int_env(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def weekday() -> int:
    """День недели для копии: 0 — понедельник … 5 — суббота, 6 — воскресенье."""
    return max(0, min(6, _int_env("RUTUTOR_BACKUP_WEEKDAY", 5)))


def hour() -> int:
    return max(0, min(23, _int_env("RUTUTOR_BACKUP_HOUR", 3)))


def tz_offset() -> int:
    """Смещение от UTC в часах. По умолчанию +5 — время Узбекистана."""
    return _int_env("RUTUTOR_BACKUP_TZ_OFFSET", 5)


def backup_chat_id() -> Optional[int]:
    """Единственный чат, куда уходит копия. Без него автокопия не включается."""
    raw = (os.getenv("RUTUTOR_BACKUP_CHAT_ID")
           or os.getenv("RUTUTOR_ALERT_CHAT_ID") or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def local_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=tz_offset())


def should_run(now: datetime, last_ymd: str) -> bool:
    """Пора ли делать копию: нужный день недели, час наступил, сегодня ещё не делали."""
    if now.weekday() != weekday():
        return False
    if now.hour < hour():
        return False
    return now.strftime("%Y-%m-%d") != (last_ymd or "")


def make_backup(bot, chat_id: int, now: Optional[datetime] = None) -> bool:
    """Снять копию и отправить файлом. Возвращает True, если получилось."""
    now = now or local_now()
    stamp = now.strftime("%Y-%m-%d")
    path = os.path.join(tempfile.gettempdir(), f"rututor_backup_{stamp}.db")

    if not storage.backup_db_to(path):
        log.warning("Не удалось снять копию базы")
        return False

    status = storage.get_db_status()
    try:
        with open(path, "rb") as f:
            bot.send_document(
                chat_id, f,
                caption=(
                    f"💾 Автоматическая копия базы · {stamp}\n\n"
                    f"Учеников: {status['users']} · "
                    f"записей XP: {status['xp_ledger_rows']} · "
                    f"уроков в кеше: {status['cached_lessons']}\n\n"
                    "Восстановление: положить файл на сервер и указать путь "
                    "в RUTUTOR_LEGACY_DB_PATH."
                ),
            )
    except Exception as e:
        log.warning("Копия снята, но отправить не удалось: %s", e)
        return False
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    storage.set_meta(LAST_BACKUP_KEY, stamp)
    log.info("Автокопия базы отправлена в чат %s", chat_id)
    return True


def _loop(bot, chat_id: int) -> None:
    while True:
        try:
            now = local_now()
            if should_run(now, storage.get_meta(LAST_BACKUP_KEY, "")):
                make_backup(bot, chat_id, now)
        except Exception as e:  # поток не должен умирать ни при каких условиях
            log.warning("Сбой в планировщике копий: %s", e)
        time.sleep(CHECK_INTERVAL_SEC)


def start(bot) -> bool:
    """Запустить фоновый планировщик. False — если чат для копий не задан."""
    chat_id = backup_chat_id()
    if chat_id is None:
        print("[backup] Автокопия выключена: не задан RUTUTOR_BACKUP_CHAT_ID. "
              "Укажите в нём свой Telegram ID (узнать: /myid).")
        return False

    threading.Thread(target=_loop, args=(bot, chat_id), daemon=True).start()
    print(f"[backup] Автокопия включена: каждую {WEEKDAY_NAMES[weekday()]} "
          f"в {hour():02d}:00 (UTC{tz_offset():+d}) в чат {chat_id}")
    return True
