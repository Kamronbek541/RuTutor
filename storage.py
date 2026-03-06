# storage.py — SQLite persistence for the Russian Tutor bot (modules + KTP curriculum + groups)
# Thread-safe, WAL mode, single connection — optimized for 40+ concurrent users.
from __future__ import annotations

import sqlite3
import time
import json
import random
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

DB_PATH = "bot.db"

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no confusing 0/O/1/I

# ── Thread-safe connection pool ──────────────────────────────────────────────
_lock = threading.Lock()
_con: Optional[sqlite3.Connection] = None


def _get_con() -> sqlite3.Connection:
    """Return the shared SQLite connection (thread-safe, WAL mode)."""
    global _con
    if _con is None:
        with _lock:
            if _con is None:
                conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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


def add_xp(user_id: int, delta: int):
    con = _get_con()
    with _lock:
        cur = con.cursor()
        cur.execute("UPDATE users SET xp = MAX(0, COALESCE(xp, 0) + ?) WHERE user_id=?", (delta, user_id))
        con.commit()


def get_user_xp(user_id: int) -> int:
    """Return current XP for the user (0 if not found)."""
    con = _get_con()
    cur = con.cursor()
    cur.execute("SELECT xp FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    return int(row[0] or 0) if row else 0


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
    con = _get_con()
    with _lock:
        cur = con.cursor()
        for tag in tags:
            if not tag or tag == "no_error":
                continue
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
    g = get_group_by_code(code)
    if not g:
        return None
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
        SELECT u.user_id, u.first_name, u.username, u.xp, u.streak, u.language_level,
               gm.role, gm.joined_ts
        FROM group_members gm
        LEFT JOIN users u ON u.user_id = gm.user_id
        WHERE gm.group_id=?
        ORDER BY gm.role DESC, u.xp DESC
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
