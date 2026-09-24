# storage.py — SQLite persistence for the Russian Tutor bot (modules + KTP curriculum + groups)
# Thread-safe, WAL mode, single connection — optimized for 40+ concurrent users.
from __future__ import annotations

import sqlite3
import time
import json
import random
import threading
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

DB_FILENAME = "bot.db"


# Каталоги, которые переживают перезапуск контейнера, если к сервису
# подключён диск (Railway Volume, Docker volume и т.п.).
VOLUME_CANDIDATES = ("/data", "/mnt/data", "/var/data", "/app/data")


def _writable_dir(path: Path) -> bool:
    """Каталог существует (или создаётся) и в него действительно можно писать."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".rututor_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def _volume_data_dir() -> Optional[Path]:
    """Подключённый постоянный диск, если он есть.

    На Railway при подключении Volume появляется RAILWAY_VOLUME_MOUNT_PATH.
    Дополнительно проверяем типовые точки монтирования — иначе база окажется
    на временном диске контейнера и сотрётся при следующем деплое.
    """
    mount = (os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "").strip()
    if mount and _writable_dir(Path(mount)):
        return Path(mount) / "rututor"

    for candidate in VOLUME_CANDIDATES:
        path = Path(candidate)
        if path.is_dir() and _writable_dir(path):
            return path / "rututor"
    return None


def _default_data_dir() -> Path:
    """Stable data folder outside release zips, so updates keep XP/cache."""
    explicit = os.getenv("RUTUTOR_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()

    volume = _volume_data_dir()
    if volume is not None:
        return volume

    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if base:
            return Path(base) / "RuTutor"

    return Path.home() / ".rututor"


def _resolve_db_path() -> Path:
    explicit = (os.getenv("RUTUTOR_DB_PATH") or os.getenv("BOT_DB_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return _default_data_dir() / DB_FILENAME


DB_PATH = str(_resolve_db_path())
_db_file_ready = False


def is_persistent_storage() -> bool:
    """Лежит ли база на диске, который переживёт перезапуск/деплой.

    На сервере без подключённого Volume ответ False — значит при каждом деплое
    пропадут ученики, XP и кеш уроков.
    """
    if os.name == "nt" or not _is_container():
        return True  # локальный запуск: диск обычный, данные не исчезают

    # Путь задан вручную — считаем, что владелец знает, куда смонтирован диск.
    if (os.getenv("RUTUTOR_DATA_DIR") or os.getenv("RUTUTOR_DB_PATH")
            or os.getenv("BOT_DB_PATH") or "").strip():
        return True

    db_path = Path(DB_PATH).expanduser().resolve()
    mount = (os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "").strip()
    roots = [Path(mount)] if mount else []
    roots += [Path(c) for c in VOLUME_CANDIDATES]
    for root in roots:
        try:
            db_path.relative_to(root.resolve())
            return True
        except Exception:
            continue
    return False


def _is_container() -> bool:
    """Грубая, но надёжная проверка «мы внутри контейнера/на сервере»."""
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"):
        return True
    if os.getenv("RUTUTOR_ASSUME_CONTAINER") == "1":
        return True
    return Path("/.dockerenv").exists()


def storage_health() -> Dict[str, Any]:
    """Короткая сводка о хранилище — для логов и админ-команды /dbstatus."""
    path = Path(DB_PATH).expanduser()
    size = 0
    try:
        size = path.stat().st_size
    except Exception:
        pass
    return {
        "db_path": str(path),
        "exists": path.exists(),
        "size_bytes": size,
        "persistent": is_persistent_storage(),
        "volume_mount": (os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "").strip(),
        "in_container": _is_container(),
    }

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no confusing 0/O/1/I

# ── Thread-safe connection pool ──────────────────────────────────────────────
_lock = threading.Lock()
_con: Optional[sqlite3.Connection] = None


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except Exception:
        return str(a) == str(b)


def _legacy_db_candidates(target: Path) -> List[Path]:
    """Find old project-local bot.db files to migrate into the stable DB path."""
    candidates: List[Path] = []
    explicit = (os.getenv("RUTUTOR_LEGACY_DB_PATH") or os.getenv("BOT_LEGACY_DB_PATH") or "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())

    here = Path(__file__).resolve().parent
    roots = [Path.cwd(), here, here.parent, Path.cwd().parent]
    # Прежнее «постоянное» место до появления поддержки Volume.
    try:
        candidates.append(Path.home() / ".rututor" / DB_FILENAME)
    except Exception:
        pass
    for root in roots:
        candidates.append(root / DB_FILENAME)
        try:
            candidates.extend(root.glob(f"*/{DB_FILENAME}"))
        except Exception:
            pass

    unique: List[Path] = []
    seen = set()
    for path in candidates:
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key in seen or _same_path(path, target):
            continue
        seen.add(key)
        unique.append(path)

    existing = [p for p in unique if p.exists() and p.is_file() and p.stat().st_size > 0]
    existing.sort(key=lambda p: (p.stat().st_mtime, p.stat().st_size), reverse=True)
    return existing


def _is_rututor_db(path: Path) -> bool:
    try:
        con = sqlite3.connect(str(path))
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('users', 'ktp_lesson_cache')")
        ok = cur.fetchone() is not None
        con.close()
        return ok
    except Exception:
        return False


def _backup_sqlite_db(src: Path, dst: Path) -> bool:
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src_con = sqlite3.connect(str(src))
        dst_con = sqlite3.connect(str(dst))
        src_con.backup(dst_con)
        dst_con.close()
        src_con.close()
        return True
    except Exception:
        return False


def _ensure_db_file_ready() -> None:
    """Create stable DB folder and migrate an old local bot.db once if found."""
    global _db_file_ready
    if _db_file_ready:
        return

    target = Path(DB_PATH).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        for candidate in _legacy_db_candidates(target):
            if _is_rututor_db(candidate) and _backup_sqlite_db(candidate, target):
                print(f"[storage] Migrated existing database: {candidate} -> {target}")
                break

    _db_file_ready = True


def get_db_path() -> str:
    """Return the actual SQLite path used by the bot."""
    return str(Path(DB_PATH).expanduser())


def get_db_status() -> Dict[str, Any]:
    """Small admin health snapshot for persistence-sensitive data."""
    con = _get_con()
    cur = con.cursor()

    def count(table: str) -> int:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return int(cur.fetchone()[0])
        except Exception:
            return 0

    return {
        "db_path": get_db_path(),
        "users": count("users"),
        "cached_lessons": count("ktp_lesson_cache"),
        "custom_topics": count("custom_topics"),
        "ktp_progress_rows": count("ktp_lesson_progress"),
        "xp_ledger_rows": count("xp_ledger"),
        "question_reports": count("question_reports"),
    }


def _get_con() -> sqlite3.Connection:
    """Return the shared SQLite connection (thread-safe, WAL mode)."""
    global _con
    if _con is None:
        with _lock:
            if _con is None:
                _ensure_db_file_ready()
                conn = sqlite3.connect(get_db_path(), check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("PRAGMA synchronous=NORMAL")
                _con = conn
    return _con


def _now_ts() -> int:
    return int(time.time())


def _rand_code(n: int = 6) -> str:
    return "".join(random.choice(_CODE_ALPHABET) for _ in range(n))


def _try_add_column(cur: sqlite3.Cursor, table: str, col: str, decl: str):
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    except Exception:
        pass


def init_db():
    """Idempotent DB init + small migrations. Call ONCE at startup."""
    con = _get_con()
    with _lock:
        cur = con.cursor()

        # ── Users & generic progress ─────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                created_ts INTEGER,
                language_level TEXT DEFAULT 'NA',
                xp INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0,
                last_active_ymd TEXT DEFAULT '',
                mode TEXT DEFAULT 'home'
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS progress (
                user_id INTEGER,
                key TEXT,
                value TEXT,
                PRIMARY KEY (user_id, key)
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS achievements (
                user_id INTEGER,
                code TEXT,
                unlocked_ts INTEGER,
                PRIMARY KEY (user_id, code)
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                ts INTEGER,
                event_type TEXT,
                payload_json TEXT
            );
        """)

        # ── Self-study modules (legacy, still supported) ──────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS module_progress (
                user_id INTEGER,
                module_id TEXT,
                level_unlocked INTEGER DEFAULT 1,
                PRIMARY KEY (user_id, module_id)
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS control_test_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                module_id TEXT,
                level INTEGER,
                score INTEGER,
                passed INTEGER,
                ts INTEGER
            );
        """)

        # ── Error tracker ────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS error_tracker (
                user_id INTEGER,
                tag TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, tag)
            );
        """)

        # ── Groups / teacher analytics ────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                group_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                join_code TEXT UNIQUE NOT NULL,
                created_ts INTEGER
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                group_id INTEGER,
                user_id INTEGER,
                role TEXT DEFAULT 'student',  -- student | teacher
                joined_ts INTEGER,
                PRIMARY KEY (group_id, user_id)
            );
        """)

        # ── KTP curriculum: cached lesson packages + per-user progress ────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ktp_lesson_cache (
                lesson_id TEXT PRIMARY KEY,
                package_json TEXT,
                created_ts INTEGER
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ktp_lesson_progress (
                user_id INTEGER,
                lesson_id TEXT,
                practice_best INTEGER DEFAULT 0,
                exam_best INTEGER DEFAULT 0,
                practice_attempts INTEGER DEFAULT 0,
                exam_attempts INTEGER DEFAULT 0,
                writing_best INTEGER DEFAULT 0,
                writing_attempts INTEGER DEFAULT 0,
                done INTEGER DEFAULT 0,
                updated_ts INTEGER,
                PRIMARY KEY (user_id, lesson_id)
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS writing_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                lesson_id TEXT,
                text TEXT,
                result_json TEXT,
                ts INTEGER
            );
        """)

        # ── XP ledger: одна строка на каждое изменение XP ─────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS xp_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ts INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'legacy',
                scope TEXT NOT NULL DEFAULT 'other',
                lesson_key TEXT NOT NULL DEFAULT '',
                note TEXT
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_xp_ledger_user_lesson ON xp_ledger(user_id, lesson_key)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_xp_ledger_user_ts ON xp_ledger(user_id, ts)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_xp_ledger_lesson ON xp_ledger(lesson_key)")
        # Защита от двойного пересчёта истории: одна backfill-строка на (ученик, урок).
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_xp_ledger_backfill
            ON xp_ledger(user_id, lesson_key) WHERE source='backfill'
        """)

        # ── Служебные флаги приложения (например, отметка о backfill) ─────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)

        # ── Настройки класса (выключатель ИИ и подсказок) ─────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS group_settings (
                group_id INTEGER,
                key TEXT,
                value TEXT,
                updated_ts INTEGER,
                PRIMARY KEY (group_id, key)
            );
        """)

        # ── Жалобы учеников на вопросы ────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS question_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                lesson_key TEXT NOT NULL DEFAULT '',
                question_id TEXT NOT NULL DEFAULT '',
                question_text TEXT,
                options_json TEXT,
                correct_text TEXT,
                comment TEXT,
                ts INTEGER,
                status TEXT NOT NULL DEFAULT 'new'
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_question_reports_status ON question_reports(status, ts)")

        # ── Custom topics (admin-uploaded documents) ────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS custom_topics (
                topic_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                lesson_id TEXT UNIQUE NOT NULL,
                semester INTEGER NOT NULL DEFAULT 4,
                created_ts INTEGER
            );
        """)
        _try_add_column(cur, "custom_topics", "semester", "INTEGER NOT NULL DEFAULT 4")

        # Migrations
        _try_add_column(cur, "users", "mode", "TEXT DEFAULT 'home'")

        con.commit()


# ── Users ────────────────────────────────────────────────────────────────────
def upsert_user(user_id: int, first_name: str, username: str):
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO users(user_id, first_name, username, created_ts)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                first_name=excluded.first_name,
                username=excluded.username;
        """, (user_id, first_name or "", username or "", _now_ts()))
        con.commit()


def ensure_user(user_id: int) -> None:
    """Создать строку пользователя, если её ещё нет, не затирая имя и XP."""
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO users(user_id, first_name, username, created_ts)"
            " VALUES (?, '', '', ?)",
            (user_id, _now_ts()),
        )
        con.commit()


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    con = _get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.row_factory = None
    return dict(row) if row else None


def set_user_mode(user_id: int, mode: str):
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("UPDATE users SET mode=? WHERE user_id=?", (mode, user_id))
        con.commit()


def set_language_level(user_id: int, level: str):
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("UPDATE users SET language_level=? WHERE user_id=?", (level, user_id))
        con.commit()


LEGACY_BUCKET_KEY = "__legacy__"   # корзина «до обновления» в журнале XP


def _scope_for(lesson_key: str) -> str:
    """Определяет раздел по ключу урока (см. award_xp)."""
    key = (lesson_key or "").strip()
    if not key:
        return "global"
    if key == LEGACY_BUCKET_KEY:
        return "other"
    if key.startswith("mod:"):
        return "module"
    if key.startswith("class:"):
        return "class"
    if key.startswith("s") and "_" in key:
        return "ktp"
    return "other"


def award_xp(
    user_id: int,
    delta: int,
    source: str,
    lesson_key: Optional[str] = None,
    note: Optional[str] = None,
    scope: Optional[str] = None,
) -> int:
    """Изменить XP ученика И записать это в журнал xp_ledger одной транзакцией.

    lesson_key: 's1_09' (КТП) | 'mod:<module>:<level>' (тренажёры) |
                'class:<YYYY-MM-DD>' (урок класса) | '' (стрик, входной тест).

    Возвращает ФАКТИЧЕСКИ применённую дельту: users.xp не уходит ниже нуля,
    поэтому списание может оказаться меньше запрошенного. В журнал пишется
    именно применённое значение — иначе сумма по урокам не сойдётся с общим XP.
    """
    key = (lesson_key or "").strip()
    try:
        delta = int(delta)
    except (TypeError, ValueError):
        return 0
    if delta == 0:
        return 0

    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("SELECT COALESCE(xp, 0) FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if not row:
            return 0  # неизвестный пользователь: ведём себя как старый add_xp
        current = int(row[0] or 0)
        applied = delta if delta >= 0 else max(delta, -current)
        if applied == 0:
            return 0
        cur.execute(
            "UPDATE users SET xp = MAX(0, COALESCE(xp, 0) + ?) WHERE user_id=?",
            (applied, user_id),
        )
        cur.execute(
            "INSERT INTO xp_ledger(user_id, ts, delta, source, scope, lesson_key, note)"
            " VALUES (?,?,?,?,?,?,?)",
            (user_id, _now_ts(), applied, source or "legacy",
             scope or _scope_for(key), key, note),
        )
        con.commit()
    return applied


def add_xp(user_id: int, delta: int):
    """Устаревшее: используйте award_xp(). Оставлено, чтобы ничего не сломалось."""
    return award_xp(user_id, delta, "legacy")


def get_user_xp(user_id: int) -> int:
    """Return current XP for the user (0 if not found)."""
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT xp FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    return int(row[0] or 0) if row else 0


# ── XP ledger: чтение ────────────────────────────────────────────────────────
def get_xp_by_lesson(user_id: int) -> List[Dict[str, Any]]:
    """[{lesson_key, scope, xp, entries, last_ts}, ...] — только ненулевые суммы."""
    con = _get_con()
    cur = con.cursor()
    cur.execute("""
        SELECT lesson_key, MIN(scope), SUM(delta), COUNT(*), MAX(ts)
        FROM xp_ledger WHERE user_id=?
        GROUP BY lesson_key
        HAVING SUM(delta) <> 0
        ORDER BY lesson_key
    """, (user_id,))
    return [
        {"lesson_key": r[0] or "", "scope": r[1] or "other",
         "xp": int(r[2] or 0), "entries": int(r[3] or 0), "last_ts": int(r[4] or 0)}
        for r in cur.fetchall()
    ]


def get_lesson_xp(user_id: int, lesson_key: str) -> int:
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT COALESCE(SUM(delta), 0) FROM xp_ledger WHERE user_id=? AND lesson_key=?",
                (user_id, (lesson_key or "").strip()))
    return int(cur.fetchone()[0] or 0)


def get_xp_by_source(user_id: int) -> List[Tuple[str, int]]:
    con = _get_con()
    cur = con.cursor()
    cur.execute("""
        SELECT source, SUM(delta) FROM xp_ledger WHERE user_id=?
        GROUP BY source ORDER BY SUM(delta) DESC
    """, (user_id,))
    return [(r[0] or "legacy", int(r[1] or 0)) for r in cur.fetchall()]


def get_xp_ledger_total(user_id: int) -> int:
    """Сумма журнала. Должна совпадать с users.xp — это самопроверка."""
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT COALESCE(SUM(delta), 0) FROM xp_ledger WHERE user_id=?", (user_id,))
    return int(cur.fetchone()[0] or 0)


def get_xp_recent(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    con = _get_con()
    cur = con.cursor()
    cur.execute("""
        SELECT ts, delta, source, lesson_key, note FROM xp_ledger
        WHERE user_id=? ORDER BY ts DESC, id DESC LIMIT ?
    """, (user_id, int(limit)))
    return [
        {"ts": int(r[0] or 0), "delta": int(r[1] or 0), "source": r[2] or "legacy",
         "lesson_key": r[3] or "", "note": r[4]}
        for r in cur.fetchall()
    ]


def get_group_lesson_xp_matrix(group_id: int) -> Dict[int, Dict[str, int]]:
    """{user_id: {lesson_key: xp}} одним запросом (без N+1 по ученикам и урокам)."""
    con = _get_con()
    cur = con.cursor()
    cur.execute("""
        SELECT l.user_id, l.lesson_key, SUM(l.delta)
        FROM xp_ledger l
        JOIN group_members gm ON gm.user_id = l.user_id
        WHERE gm.group_id=? AND gm.role='student'
        GROUP BY l.user_id, l.lesson_key
    """, (group_id,))
    out: Dict[int, Dict[str, int]] = {}
    for uid, key, total in cur.fetchall():
        out.setdefault(int(uid), {})[key or ""] = int(total or 0)
    return out


def get_group_lesson_xp_totals(group_id: int) -> List[Tuple[str, int, int]]:
    """[(lesson_key, сумма XP класса, сколько учеников получали XP), ...]."""
    con = _get_con()
    cur = con.cursor()
    cur.execute("""
        SELECT l.lesson_key, SUM(l.delta), COUNT(DISTINCT l.user_id)
        FROM xp_ledger l
        JOIN group_members gm ON gm.user_id = l.user_id
        WHERE gm.group_id=? AND gm.role='student'
        GROUP BY l.lesson_key
        HAVING SUM(l.delta) <> 0
        ORDER BY SUM(l.delta) DESC
    """, (group_id,))
    return [(r[0] or "", int(r[1] or 0), int(r[2] or 0)) for r in cur.fetchall()]


def export_group_xp_rows(group_id: int, lesson_ids: List[str]) -> List[Dict[str, Any]]:
    """Строки для CSV: по ученику — общий XP и XP по каждому уроку."""
    members = [m for m in get_group_members(group_id) if (m.get("role") or "student") == "student"]
    matrix = get_group_lesson_xp_matrix(group_id)
    rows: List[Dict[str, Any]] = []
    for m in members:
        uid = int(m["user_id"])
        by_key = matrix.get(uid, {})
        row: Dict[str, Any] = {
            "user_id": uid,
            "first_name": m.get("first_name") or "",
            "username": m.get("username") or "",
            "xp_total": int(m.get("xp") or 0),
            "xp_ledger_total": sum(by_key.values()),
        }
        for lid in lesson_ids:
            row[f"xp_{lid}"] = by_key.get(lid, 0)
        row["xp_legacy"] = by_key.get(LEGACY_BUCKET_KEY, 0)
        known = set(lesson_ids) | {LEGACY_BUCKET_KEY}
        row["xp_other"] = sum(v for k, v in by_key.items() if k not in known)
        rows.append(row)
    rows.sort(key=lambda r: r["xp_total"], reverse=True)
    return rows


# ── Служебные флаги ──────────────────────────────────────────────────────────
def get_meta(key: str, default: str = "") -> str:
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT value FROM app_meta WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else default


def set_meta(key: str, value: str) -> None:
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO app_meta(key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        con.commit()


def update_streak(user_id: int, today_ymd: str) -> Tuple[int, bool]:
    """Returns (streak, changed). Resets streak if user skipped a day."""
    con = _get_con()
    with _lock:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT streak, last_active_ymd FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        con.row_factory = None
        if not row:
            return 0, False

        streak = int(row["streak"] or 0)
        last = (row["last_active_ymd"] or "")

        if last == today_ymd:
            return streak, False

        # Check if last_active was yesterday → continue streak, else reset
        if last:
            try:
                last_date = datetime.strptime(last, "%Y-%m-%d").date()
                today_date = datetime.strptime(today_ymd, "%Y-%m-%d").date()
                if today_date - last_date == timedelta(days=1):
                    new_streak = streak + 1
                else:
                    new_streak = 1  # skipped a day → reset
            except ValueError:
                new_streak = 1
        else:
            new_streak = 1

        cur.execute("UPDATE users SET streak=?, last_active_ymd=? WHERE user_id=?",
                    (new_streak, today_ymd, user_id))
        con.commit()
        return new_streak, True


# ── Generic KV progress ───────────────────────────────────────────────────────
def set_progress(user_id: int, key: str, value: str):
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO progress(user_id, key, value)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value
        """, (user_id, key, value))
        con.commit()


def get_progress(user_id: int, key: str, default: str = "") -> str:
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT value FROM progress WHERE user_id=? AND key=?", (user_id, key))
    row = cur.fetchone()
    return row[0] if row else default


def clear_progress_prefix(user_id: int, prefix: str):
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("DELETE FROM progress WHERE user_id=? AND key LIKE ?", (user_id, f"{prefix}%"))
        con.commit()


def clear_progress_key(user_id: int, key: str):
    """Delete one progress key for a user."""
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("DELETE FROM progress WHERE user_id=? AND key=?", (user_id, key))
        con.commit()


def clear_lesson_session(user_id: int):
    """Reset temporary lesson/writing/test state without touching XP or history."""
    prefixes = (
        "test_", "lesson_", "ctrl_", "open_", "ktp_", "hw_",
        "task_state_", "class_lesson_", "cls_"
    )
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute(
            "DELETE FROM progress WHERE user_id=? AND (" +
            " OR ".join(["key LIKE ?" for _ in prefixes]) +
            ")",
            (user_id, *[f"{p}%" for p in prefixes])
        )
        cur.execute(
            "DELETE FROM progress WHERE user_id=? AND key IN ('last_clean_msg', 'last_status_msg')",
            (user_id,)
        )
        cur.execute(
            "INSERT INTO progress(user_id, key, value) VALUES (?, 'mode', 'idle') "
            "ON CONFLICT(user_id, key) DO UPDATE SET value='idle'",
            (user_id,)
        )
        con.commit()


# ── Events / achievements ─────────────────────────────────────────────────────
def log_event(user_id: int, event_type: str, payload_json: str):
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("INSERT INTO events(user_id, ts, event_type, payload_json) VALUES (?,?,?,?)",
                    (user_id, _now_ts(), event_type, payload_json))
        con.commit()


def unlock_achievement(user_id: int, code: str) -> bool:
    con = _get_con()
    with _lock:
        cur = con.cursor()
        try:
            cur.execute("INSERT INTO achievements(user_id, code, unlocked_ts) VALUES (?,?,?)",
                        (user_id, code, _now_ts()))
            con.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def get_achievements(user_id: int) -> List[str]:
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT code FROM achievements WHERE user_id=? ORDER BY unlocked_ts ASC", (user_id,))
    return [r[0] for r in cur.fetchall()]


# ── Module progress (legacy) ──────────────────────────────────────────────────
def get_module_level_unlocked(user_id: int, module_id: str) -> int:
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT level_unlocked FROM module_progress WHERE user_id=? AND module_id=?",
                (user_id, module_id))
    row = cur.fetchone()
    return int(row[0]) if row else 1


def set_module_level_unlocked(user_id: int, module_id: str, level: int):
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO module_progress(user_id, module_id, level_unlocked)
            VALUES (?,?,?)
            ON CONFLICT(user_id, module_id) DO UPDATE SET
              level_unlocked=MAX(excluded.level_unlocked, level_unlocked)
        """, (user_id, module_id, int(level)))
        con.commit()


def is_level_tasks_done(user_id: int, module_id: str, level: int) -> bool:
    return get_progress(user_id, f"tasks_done_{module_id}_{level}", "0") == "1"


def mark_level_tasks_done(user_id: int, module_id: str, level: int):
    set_progress(user_id, f"tasks_done_{module_id}_{level}", "1")


def is_level_ctrl_passed(user_id: int, module_id: str, level: int) -> bool:
    return get_progress(user_id, f"ctrl_passed_{module_id}_{level}", "0") == "1"


def mark_level_ctrl_passed(user_id: int, module_id: str, level: int):
    set_progress(user_id, f"ctrl_passed_{module_id}_{level}", "1")


def record_control_test(user_id: int, module_id: str, level: int, score: int, passed: bool):
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO control_test_results(user_id, module_id, level, score, passed, ts)
            VALUES (?,?,?,?,?,?)
        """, (user_id, module_id, int(level), int(score), 1 if passed else 0, _now_ts()))
        con.commit()


def get_control_test_attempts(user_id: int, module_id: str, level: int) -> int:
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM control_test_results WHERE user_id=? AND module_id=? AND level=?",
                (user_id, module_id, int(level)))
    row = cur.fetchone()
    return int(row[0]) if row else 0


# ── Error tracker ─────────────────────────────────────────────────────────────
def track_errors(user_id: int, tags: List[str]):
    if not tags:
        return
    from utils import split_tags
    clean_tags = [t for t in split_tags(tags) if t and t != "no_error"]
    if not clean_tags:
        return
    con = _get_con()
    with _lock:
        cur = con.cursor()
        for tag in clean_tags:
            cur.execute("""
                INSERT INTO error_tracker(user_id, tag, count) VALUES (?,?,1)
                ON CONFLICT(user_id, tag) DO UPDATE SET count = count + 1
            """, (user_id, tag))
        con.commit()


def get_top_errors(user_id: int, limit: int = 3) -> List[Tuple[str, int]]:
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT tag, count FROM error_tracker WHERE user_id=? ORDER BY count DESC LIMIT ?",
                (user_id, int(limit)))
    return cur.fetchall()


# ── Legacy class helpers (kept for backward compatibility) ────────────────────
def is_class_lesson_done(user_id: int, lesson_key: str) -> bool:
    return get_progress(user_id, f"cls_done_{lesson_key}", "0") == "1"


def mark_class_lesson_done(user_id: int, lesson_key: str):
    set_progress(user_id, f"cls_done_{lesson_key}", "1")


def is_homework_submitted(user_id: int, lesson_key: str) -> bool:
    return get_progress(user_id, f"hw_done_{lesson_key}", "0") == "1"


def mark_homework_done(user_id: int, lesson_key: str):
    set_progress(user_id, f"hw_done_{lesson_key}", "1")


def get_class_lesson_score(user_id: int, lesson_key: str) -> int:
    try:
        return int(get_progress(user_id, f"cls_score_{lesson_key}", "0"))
    except Exception:
        return 0


def set_class_lesson_score(user_id: int, lesson_key: str, score: int):
    set_progress(user_id, f"cls_score_{lesson_key}", str(int(score)))


def increment_hint_used(user_id: int) -> int:
    cur = int(get_progress(user_id, "hints_used_today", "0") or 0)
    cur += 1
    set_progress(user_id, "hints_used_today", str(cur))
    return cur


# ── KTP cache & progress ──────────────────────────────────────────────────────
def get_ktp_cache(lesson_id: str) -> Optional[Dict[str, Any]]:
    con = _get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT package_json FROM ktp_lesson_cache WHERE lesson_id=?", (lesson_id,))
    row = cur.fetchone()
    con.row_factory = None
    if not row:
        return None
    try:
        return json.loads(row["package_json"])
    except Exception:
        return None


def set_ktp_cache(lesson_id: str, package: Dict[str, Any]) -> None:
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO ktp_lesson_cache(lesson_id, package_json, created_ts)
            VALUES (?,?,?)
            ON CONFLICT(lesson_id) DO UPDATE SET
              package_json=excluded.package_json,
              created_ts=excluded.created_ts
        """, (lesson_id, json.dumps(package, ensure_ascii=False), _now_ts()))
        con.commit()


def get_ktp_progress(user_id: int, lesson_id: str) -> Dict[str, Any]:
    con = _get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM ktp_lesson_progress WHERE user_id=? AND lesson_id=?",
                (user_id, lesson_id))
    row = cur.fetchone()
    con.row_factory = None
    if not row:
        return {"done": 0, "practice_best": 0, "exam_best": 0, "writing_best": 0, "practice_attempts": 0, "exam_attempts": 0}
    return dict(row)


def upsert_ktp_progress(
    user_id: int,
    lesson_id: str,
    practice_score: Optional[int] = None,
    exam_score: Optional[int] = None,
    writing_score: Optional[int] = None,
    done: Optional[bool] = None,
) -> None:
    """Update best scores + attempts."""
    con = _get_con()
    with _lock:
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        cur.execute("SELECT * FROM ktp_lesson_progress WHERE user_id=? AND lesson_id=?",
                    (user_id, lesson_id))
        row = cur.fetchone()
        prev = dict(row) if row else None
        con.row_factory = None

        practice_best = int(prev["practice_best"]) if prev else 0
        exam_best = int(prev["exam_best"]) if prev else 0
        writing_best = int(prev["writing_best"]) if prev else 0
        practice_attempts = int(prev["practice_attempts"]) if prev else 0
        exam_attempts = int(prev["exam_attempts"]) if prev else 0
        writing_attempts = int(prev["writing_attempts"]) if prev else 0
        done_prev = int(prev["done"]) if prev else 0

        if practice_score is not None:
            practice_attempts += 1
            practice_best = max(practice_best, int(practice_score))
        if exam_score is not None:
            exam_attempts += 1
            exam_best = max(exam_best, int(exam_score))
        if writing_score is not None:
            writing_attempts += 1
            writing_best = max(writing_best, int(writing_score))

        done_val = done_prev
        if done is not None:
            done_val = max(done_prev, 1 if done else 0)

        cur.execute("""
            INSERT INTO ktp_lesson_progress(
              user_id, lesson_id, practice_best, exam_best,
              practice_attempts, exam_attempts,
              writing_best, writing_attempts,
              done, updated_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id, lesson_id) DO UPDATE SET
              practice_best=excluded.practice_best,
              exam_best=excluded.exam_best,
              practice_attempts=excluded.practice_attempts,
              exam_attempts=excluded.exam_attempts,
              writing_best=excluded.writing_best,
              writing_attempts=excluded.writing_attempts,
              done=excluded.done,
              updated_ts=excluded.updated_ts
        """, (
            user_id, lesson_id,
            practice_best, exam_best,
            practice_attempts, exam_attempts,
            writing_best, writing_attempts,
            done_val, _now_ts()
        ))
        con.commit()


def save_writing_submission(user_id: int, lesson_id: str, text: str, result: Dict[str, Any]):
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO writing_submissions(user_id, lesson_id, text, result_json, ts)
            VALUES (?,?,?,?,?)
        """, (user_id, lesson_id, text, json.dumps(result, ensure_ascii=False), _now_ts()))
        con.commit()


def count_ktp_done(user_id: int) -> int:
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM ktp_lesson_progress WHERE user_id=? AND done=1", (user_id,))
    return int(cur.fetchone()[0])


# ── Admin/teacher analytics ───────────────────────────────────────────────────
def count_users() -> int:
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    return int(cur.fetchone()[0])


def count_active_on(ymd: str) -> int:
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE last_active_ymd=?", (ymd,))
    return int(cur.fetchone()[0])


def create_group(name: str) -> dict:
    """Create a class group and return {'group_id','name','join_code'}."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Group name is empty")
    con = _get_con()
    with _lock:
        cur = con.cursor()
        for _ in range(50):
            code = _rand_code(6)
            try:
                cur.execute("INSERT INTO groups(name, join_code, created_ts) VALUES (?,?,?)",
                            (name, code, _now_ts()))
                con.commit()
                gid = cur.lastrowid
                return {"group_id": gid, "name": name, "join_code": code}
            except sqlite3.IntegrityError:
                continue
    raise RuntimeError("Failed to generate unique join code")


def list_groups() -> List[Dict[str, Any]]:
    con = _get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM groups ORDER BY created_ts DESC")
    groups = [dict(r) for r in cur.fetchall()]
    con.row_factory = None
    for g in groups:
        gid = g["group_id"]
        cur.execute("SELECT COUNT(*) FROM group_members WHERE group_id=?", (gid,))
        g["members_count"] = int(cur.fetchone()[0])
    return groups


def get_group(group_id: int) -> Optional[Dict[str, Any]]:
    con = _get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM groups WHERE group_id=?", (group_id,))
    row = cur.fetchone()
    con.row_factory = None
    return dict(row) if row else None


def get_group_by_code(code: str) -> Optional[Dict[str, Any]]:
    code = (code or "").strip().upper()
    if not code:
        return None
    con = _get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM groups WHERE join_code=?", (code,))
    row = cur.fetchone()
    con.row_factory = None
    return dict(row) if row else None


def add_member(group_id: int, user_id: int, role: str = "student") -> None:
    role = (role or "student").strip().lower()
    if role not in ("student", "teacher"):
        role = "student"
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO group_members(group_id, user_id, role, joined_ts)
            VALUES (?,?,?,?)
            ON CONFLICT(group_id, user_id) DO UPDATE SET role=excluded.role
        """, (group_id, user_id, role, _now_ts()))
        con.commit()


def join_group_by_code(user_id: int, code: str, role: str = "student") -> Optional[Dict[str, Any]]:
    """Вступить в класс по коду. Строка в users создаётся сразу, иначе участник
    без единого /start остаётся «призраком» и ломает отчёты по классу."""
    g = get_group_by_code(code)
    if not g:
        return None
    ensure_user(user_id)
    add_member(int(g["group_id"]), user_id, role=role)
    return g


def get_user_groups(user_id: int) -> List[Dict[str, Any]]:
    con = _get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("""
        SELECT g.*, gm.role
        FROM group_members gm
        JOIN groups g ON g.group_id=gm.group_id
        WHERE gm.user_id=?
        ORDER BY g.created_ts DESC
    """, (user_id,))
    rows = [dict(r) for r in cur.fetchall()]
    con.row_factory = None
    return rows


def get_group_members(group_id: int) -> List[Dict[str, Any]]:
    con = _get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("""
        SELECT gm.user_id AS user_id,
               COALESCE(u.first_name, '') AS first_name,
               COALESCE(u.username, '') AS username,
               COALESCE(u.xp, 0) AS xp,
               COALESCE(u.streak, 0) AS streak,
               COALESCE(u.language_level, 'NA') AS language_level,
               gm.role, gm.joined_ts
        FROM group_members gm
        LEFT JOIN users u ON u.user_id = gm.user_id
        WHERE gm.group_id=?
        ORDER BY gm.role DESC, COALESCE(u.xp, 0) DESC
    """, (group_id,))
    rows = [dict(r) for r in cur.fetchall()]
    con.row_factory = None
    return rows


def get_group_summary(group_id: int) -> Dict[str, Any]:
    """Basic stats for a group (students/teachers, avg xp, avg lessons done)."""
    members = get_group_members(group_id)
    students = [m for m in members if m.get("role") == "student"]
    teachers = [m for m in members if m.get("role") == "teacher"]
    xp_avg = int(round(sum(int(m.get("xp") or 0) for m in students) / max(1, len(students))))
    # avg done lessons
    if not students:
        done_avg = 0
    else:
        done_avg = int(round(sum(count_ktp_done(int(m["user_id"])) for m in students) / len(students)))
    return {"students": len(students), "teachers": len(teachers), "xp_avg": xp_avg, "ktp_done_avg": done_avg}


def get_group_top_errors(group_id: int, limit: int = 8) -> List[Tuple[str, int]]:
    """Aggregate error tags for all students in the group."""
    con = _get_con()
    cur = con.cursor()
    cur.execute("""
        SELECT et.tag, SUM(et.count) as total
        FROM error_tracker et
        JOIN group_members gm ON gm.user_id = et.user_id
        WHERE gm.group_id=? AND gm.role='student'
        GROUP BY et.tag
        ORDER BY total DESC
        LIMIT ?
    """, (group_id, int(limit)))
    return [(r[0], int(r[1])) for r in cur.fetchall()]


def export_group_progress_rows(group_id: int, lesson_ids: List[str]) -> List[Dict[str, Any]]:
    """Rows for CSV export: one row per student, plus per-lesson done/exam_best."""
    members = [m for m in get_group_members(group_id) if m.get("role") == "student"]
    rows: List[Dict[str, Any]] = []
    for m in members:
        uid = int(m["user_id"])
        row = {
            "user_id": uid,
            "first_name": m.get("first_name") or "",
            "username": m.get("username") or "",
            "xp": int(m.get("xp") or 0),
            "streak": int(m.get("streak") or 0),
            "language_level": m.get("language_level") or "",
            "lessons_done": count_ktp_done(uid),
        }
        # per lesson: done and best exam
        for lid in lesson_ids:
            p = get_ktp_progress(uid, lid)
            row[f"{lid}_done"] = int(p.get("done") or 0)
            row[f"{lid}_exam_best"] = int(p.get("exam_best") or 0)
        rows.append(row)
    return rows


def get_top_users(limit: int = 10) -> List[Dict[str, Any]]:
    """Global XP leaderboard."""
    con = _get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT user_id, first_name, username, xp FROM users ORDER BY xp DESC LIMIT ?", (int(limit),))
    rows = [dict(r) for r in cur.fetchall()]
    con.row_factory = None
    return rows


def get_group_leaderboard(group_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """Group XP leaderboard (students only)."""
    con = _get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("""
        SELECT u.user_id, u.first_name, u.username, u.xp
        FROM users u
        JOIN group_members gm ON gm.user_id=u.user_id
        WHERE gm.group_id=? AND gm.role='student'
        ORDER BY u.xp DESC
        LIMIT ?
    """, (int(group_id), int(limit)))
    rows = [dict(r) for r in cur.fetchall()]
    con.row_factory = None
    return rows


# ── Custom topics (admin document upload) ─────────────────────────────────────
def create_custom_topic(name: str, semester: int = 4) -> Dict[str, Any]:
    """Create a custom topic and return {topic_id, name, lesson_id, semester}."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Topic name is empty")
    con = _get_con()
    with _lock:
        cur = con.cursor()
        # Count existing custom topics to generate lesson_id
        cur.execute("SELECT COUNT(*) FROM custom_topics")
        count = int(cur.fetchone()[0])
        lesson_id = f"custom_{count + 1:03d}"
        cur.execute(
            "INSERT INTO custom_topics(name, lesson_id, semester, created_ts) VALUES (?,?,?,?)",
            (name, lesson_id, int(semester), _now_ts()),
        )
        con.commit()
        topic_id = cur.lastrowid
        return {"topic_id": topic_id, "name": name, "lesson_id": lesson_id, "semester": semester}


def list_custom_topics(semester: Optional[int] = None) -> List[Dict[str, Any]]:
    """List custom topics, optionally filtered by semester."""
    con = _get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    if semester is not None:
        cur.execute("SELECT * FROM custom_topics WHERE semester=? ORDER BY created_ts DESC", (int(semester),))
    else:
        cur.execute("SELECT * FROM custom_topics ORDER BY created_ts DESC")
    rows = [dict(r) for r in cur.fetchall()]
    con.row_factory = None
    return rows


def get_custom_topic(topic_id: int) -> Optional[Dict[str, Any]]:
    con = _get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM custom_topics WHERE topic_id=?", (topic_id,))
    row = cur.fetchone()
    con.row_factory = None
    return dict(row) if row else None


def get_custom_semesters() -> List[int]:
    """Get list of distinct semesters that have custom topics."""
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT DISTINCT semester FROM custom_topics ORDER BY semester")
    return [row[0] for row in cur.fetchall()]


def save_custom_exercises(lesson_id: str, package: Dict[str, Any]) -> None:
    """Save parsed exercises as a KTP lesson cache entry."""
    set_ktp_cache(lesson_id, package)


# ── Настройки класса: выключатель ИИ и подсказок ─────────────────────────────
GROUP_SETTING_DEFAULTS: Dict[str, str] = {
    "ai_enabled": "1",
    "hints_enabled": "1",
}


def get_group_setting(group_id: int, key: str, default: Optional[str] = None) -> str:
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT value FROM group_settings WHERE group_id=? AND key=?", (group_id, key))
    row = cur.fetchone()
    if row and row[0] is not None:
        return str(row[0])
    if default is not None:
        return default
    return GROUP_SETTING_DEFAULTS.get(key, "")


def set_group_setting(group_id: int, key: str, value: str) -> None:
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO group_settings(group_id, key, value, updated_ts) VALUES (?,?,?,?)
            ON CONFLICT(group_id, key) DO UPDATE SET
                value=excluded.value, updated_ts=excluded.updated_ts
        """, (group_id, key, str(value), _now_ts()))
        con.commit()


def _feature_enabled_for(user_id: int, key: str) -> bool:
    """Запрет строже разрешения: если хоть в одном классе ученика выключено — выключено.

    Учителей ограничение не касается: роль 'teacher' в группе не учитывается,
    чтобы учитель мог сам проверить задание с включённым ИИ.
    """
    con = _get_con()
    cur = con.cursor()
    cur.execute("""
        SELECT gs.value FROM group_members gm
        JOIN group_settings gs ON gs.group_id = gm.group_id AND gs.key = ?
        WHERE gm.user_id = ? AND COALESCE(gm.role, 'student') = 'student'
    """, (key, user_id))
    for (value,) in cur.fetchall():
        if str(value) == "0":
            return False
    return True


def is_ai_enabled_for(user_id: int) -> bool:
    """Разрешён ли ученику встроенный ИИ (письменные задания)."""
    return _feature_enabled_for(user_id, "ai_enabled")


def are_hints_enabled_for(user_id: int) -> bool:
    """Разрешены ли ученику подсказки."""
    return _feature_enabled_for(user_id, "hints_enabled")


# ── Жалобы на вопросы ────────────────────────────────────────────────────────
def add_question_report(
    user_id: int,
    lesson_key: str,
    question: Dict[str, Any],
    correct_text: str = "",
    comment: str = "",
) -> int:
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO question_reports(
                user_id, lesson_key, question_id, question_text,
                options_json, correct_text, comment, ts, status)
            VALUES (?,?,?,?,?,?,?,?,'new')
        """, (
            user_id,
            (lesson_key or "").strip(),
            str((question or {}).get("id") or ""),
            str((question or {}).get("q") or ""),
            json.dumps((question or {}).get("options") or [], ensure_ascii=False),
            correct_text or "",
            comment or "",
            _now_ts(),
        ))
        con.commit()
        return int(cur.lastrowid)


def has_recent_question_report(user_id: int, question_text: str, within_sec: int = 3600) -> bool:
    """Защита от случайного двойного нажатия «Ошибка в вопросе»."""
    con = _get_con()
    cur = con.cursor()
    cur.execute("""
        SELECT 1 FROM question_reports
        WHERE user_id=? AND question_text=? AND ts >= ?
        LIMIT 1
    """, (user_id, question_text or "", _now_ts() - int(within_sec)))
    return cur.fetchone() is not None


def list_question_reports(status: str = "new", limit: int = 20) -> List[Dict[str, Any]]:
    con = _get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    if status == "all":
        cur.execute("SELECT * FROM question_reports ORDER BY ts DESC LIMIT ?", (int(limit),))
    else:
        cur.execute("SELECT * FROM question_reports WHERE status=? ORDER BY ts DESC LIMIT ?",
                    (status, int(limit)))
    rows = [dict(r) for r in cur.fetchall()]
    con.row_factory = None
    return rows


def count_question_reports(status: str = "new") -> int:
    con = _get_con()
    cur = con.cursor()
    if status == "all":
        cur.execute("SELECT COUNT(*) FROM question_reports")
    else:
        cur.execute("SELECT COUNT(*) FROM question_reports WHERE status=?", (status,))
    return int(cur.fetchone()[0] or 0)


def resolve_question_report(report_id: int) -> None:
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("UPDATE question_reports SET status='done' WHERE id=?", (int(report_id),))
        con.commit()


# ── Разовый пересчёт истории XP по урокам ────────────────────────────────────
XP_BACKFILL_KEY = "xp_ledger_backfill_v1"


def _writing_xp_history(user_id: int, lesson_id: str) -> int:
    """Проигрывает письменные попытки ученика по уроку и суммирует XP за них."""
    import xp_rules

    con = _get_con()
    cur = con.cursor()
    cur.execute("""
        SELECT result_json FROM writing_submissions
        WHERE user_id=? AND lesson_id=? ORDER BY ts ASC, id ASC
    """, (user_id, lesson_id))
    rows = cur.fetchall()
    if not rows:
        return 0

    total = 0
    best = 0
    for i, (result_json,) in enumerate(rows):
        try:
            result = json.loads(result_json or "{}")
        except Exception:
            result = {}
        scores = result.get("scores") or {}
        overall = int(scores.get("overall", result.get("score", 0)) or 0)
        coherence = int(scores.get("coherence", 1) or 0)
        total += xp_rules.ktp_writing_xp(i == 0, overall, coherence, best)
        best = max(best, overall)
    return total


def backfill_xp_ledger_once(force: bool = False) -> Dict[str, int]:
    """Восстановить историю XP по урокам для учеников, которые занимались до журнала.

    Идемпотентно: вызывается при каждом старте бота, но выполняется один раз.
    Инвариант после выполнения: SUM(xp_ledger.delta) == users.xp для каждого ученика.
    """
    import xp_rules

    _get_con()  # гарантируем, что база готова
    if get_meta(XP_BACKFILL_KEY) and not force:
        return {"skipped": 1, "users": 0, "lesson_rows": 0, "legacy_rows": 0}

    con = _get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT user_id, COALESCE(xp, 0) AS xp FROM users")
    users = [(int(r["user_id"]), int(r["xp"] or 0)) for r in cur.fetchall()]
    con.row_factory = None

    stats = {"skipped": 0, "users": 0, "lesson_rows": 0, "legacy_rows": 0}

    for user_id, user_xp in users:
        if force:
            with _lock:
                con.cursor().execute(
                    "DELETE FROM xp_ledger WHERE user_id=? AND source='backfill'", (user_id,))
                con.commit()

        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT * FROM ktp_lesson_progress WHERE user_id=?", (user_id,))
        progress_rows = [dict(r) for r in cur.fetchall()]
        con.row_factory = None

        for row in progress_rows:
            lesson_id = str(row.get("lesson_id") or "")
            if not lesson_id:
                continue
            practice_best = int(row.get("practice_best") or 0)
            exam_best = int(row.get("exam_best") or 0)

            practice_xp = practice_best * xp_rules.XP_KTP_PRACTICE_PER_POINT

            pack = get_ktp_cache(lesson_id) or {}
            exam_total = len(pack.get("exam") or []) or 8
            exam_xp = exam_best * xp_rules.XP_KTP_EXAM_PER_POINT
            if exam_best >= xp_rules.exam_pass_threshold(exam_total):
                exam_xp += xp_rules.XP_KTP_EXAM_FIRST_PASS

            writing_xp = _writing_xp_history(user_id, lesson_id)
            if not writing_xp and int(row.get("writing_attempts") or 0) > 0:
                writing_xp = (
                    xp_rules.XP_KTP_WRITING_FIRST_BASE
                    + int(row.get("writing_best") or 0) * xp_rules.XP_KTP_WRITING_PER_OVERALL
                )

            lesson_xp = practice_xp + exam_xp + writing_xp
            if lesson_xp <= 0:
                continue

            note = json.dumps(
                {"practice": practice_xp, "exam": exam_xp, "writing": writing_xp},
                ensure_ascii=False,
            )
            with _lock:
                c = con.cursor()
                c.execute("""
                    INSERT OR IGNORE INTO xp_ledger(
                        user_id, ts, delta, source, scope, lesson_key, note)
                    VALUES (?,?,?,?,?,?,?)
                """, (user_id, int(row.get("updated_ts") or _now_ts()), lesson_xp,
                      xp_rules.SRC_BACKFILL, "ktp", lesson_id, note))
                if c.rowcount:
                    stats["lesson_rows"] += 1
                con.commit()

        # Остаток считаем ПОСЛЕ вставки уроков и по всему журналу,
        # поэтому порядок вызова относительно живых начислений не важен.
        remainder = user_xp - get_xp_ledger_total(user_id)
        if remainder != 0:
            with _lock:
                c = con.cursor()
                c.execute("""
                    INSERT OR IGNORE INTO xp_ledger(
                        user_id, ts, delta, source, scope, lesson_key, note)
                    VALUES (?,?,?,?,?,?,?)
                """, (user_id, _now_ts(), remainder, xp_rules.SRC_BACKFILL,
                      "other", LEGACY_BUCKET_KEY, "до обновления"))
                if c.rowcount:
                    stats["legacy_rows"] += 1
                con.commit()

        stats["users"] += 1

    set_meta(XP_BACKFILL_KEY, str(_now_ts()))
    return stats


def backup_db_to(dst_path: str) -> bool:
    """Скопировать базу через SQLite backup (безопасно при включённом WAL)."""
    try:
        src = Path(get_db_path())
        if not src.exists():
            return False
        dst = Path(dst_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        with _lock:
            dst_con = sqlite3.connect(str(dst))
            _get_con().backup(dst_con)
            dst_con.close()
        return True
    except Exception:
        return False
