# admin.py — Admin + teacher panels (groups, broadcast, export)
from __future__ import annotations

import os
import csv
import tempfile
from datetime import datetime
from typing import Optional, List

from dotenv import load_dotenv
load_dotenv()

import storage
from ktp_plan import KTP_LESSONS
from utils import format_error_stats


def _parse_ids(env_val: str) -> set[int]:
    ids = set()
    for part in (env_val or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except Exception:
            pass
    return ids


# Read from .env; fallback to hardcoded defaults
_env_admins = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = _parse_ids(_env_admins) if _env_admins.strip() else {460793063, 502483421, 107713886}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _now_ymd() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _kb_one_col(buttons):
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(row_width=1)
    for text, data in buttons:
        kb.add(InlineKeyboardButton(text, callback_data=data))
    return kb


def _kb(*rows):
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup()
    for row in rows:
        kb.row(*[InlineKeyboardButton(text, callback_data=data) for text, data in row])
    return kb


def register(bot):
    # ── /admin ────────────────────────────────────────────────────────────
    @bot.message_handler(commands=["admin"])
    def on_admin_cmd(msg):
        uid = msg.from_user.id
        if not is_admin(uid):
            bot.reply_to(msg, "⛔️ Доступ запрещён.")
            return

        bot.send_message(
            msg.chat.id,
            "🛠 <b>Админ-панель</b>\n\nВыбери действие:",
            reply_markup=_admin_menu_kb(),
            parse_mode="HTML",
        )

    def _admin_menu_kb():
        return _kb(
            [("👥 Пользователи", "admin:users"), ("🏫 Классы", "admin:groups")],
            [("📢 Рассылка", "admin:broadcast"), ("📤 Экспорт CSV", "admin:export")],
            [("❌ Закрыть", "admin:close")],
        )

    @bot.callback_query_handler(func=lambda c: c.data == "admin:close")
    def on_close(call):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id)
            return
        bot.answer_callback_query(call.id)
        bot.edit_message_text("✅ Админ-панель закрыта.", call.message.chat.id, call.message.message_id)

    # ── Users ─────────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "admin:users")
    def on_users(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        total = storage.count_users()
        active = storage.count_active_on(_now_ymd())
        groups = len(storage.list_groups())
        text = (
            "👥 <b>Пользователи</b>\n\n"
            f"Всего: <b>{total}</b>\n"
            f"Активны сегодня: <b>{active}</b>\n"
            f"Классов: <b>{groups}</b>\n"
        )
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=_kb([("🏫 Классы", "admin:groups"), ("⬅️ Назад", "admin:menu")]),
            parse_mode="HTML",
        )

    @bot.callback_query_handler(func=lambda c: c.data == "admin:menu")
    def on_menu(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "🛠 <b>Админ-панель</b>\n\nВыбери действие:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=_admin_menu_kb(),
            parse_mode="HTML",
        )

    # ── Groups ────────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "admin:groups")
    def on_groups(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        groups = storage.list_groups()
        buttons = []
        for g in groups[:25]:
            buttons.append((f"🏫 {g['name']} ({g.get('members_count',0)})", f"admin:group:{g['group_id']}"))
        buttons.append(("➕ Создать класс", "admin:group_create"))
        buttons.append(("⬅️ Назад", "admin:menu"))
        bot.edit_message_text(
            "🏫 <b>Классы</b>\n\nВыбери класс или создай новый:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=_kb_one_col(buttons),
            parse_mode="HTML",
        )

    @bot.callback_query_handler(func=lambda c: c.data == "admin:group_create")
    def on_group_create(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        storage.set_progress(uid, "admin_state", "await_group_name")
        bot.send_message(call.message.chat.id, "✍️ Напиши название класса (например: <b>1UzG1</b>).", parse_mode="HTML")

    @bot.message_handler(func=lambda m: is_admin(m.from_user.id) and storage.get_progress(m.from_user.id, "admin_state") == "await_group_name")
    def on_group_name(message):
        uid = message.from_user.id
        name = (message.text or "").strip()
        if len(name) < 2:
            bot.reply_to(message, "Название слишком короткое. Попробуй ещё раз.")
            return
        try:
            g = storage.create_group(name)
        except Exception as e:
            bot.reply_to(message, f"⚠️ Не удалось создать класс: {e}")
            storage.set_progress(uid, "admin_state", "")
            return
        storage.set_progress(uid, "admin_state", "")
        bot.send_message(
            message.chat.id,
            "✅ <b>Класс создан!</b>\n\n"
            f"Название: <b>{g['name']}</b>\n"
            f"Код для входа (ученикам): <code>{g['join_code']}</code>\n\n"
            "Ученики заходят командой:\n"
            f"<code>/join {g['join_code']}</code>\n\n"
            "Учитель заходит командой:\n"
            f"<code>/teach {g['join_code']}</code>",
            parse_mode="HTML",
            reply_markup=_kb_one_col([
                ("📊 Открыть класс", f"admin:group:{g['group_id']}"),
                ("🏫 К списку", "admin:groups"),
            ]),
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("admin:group:"))
    def on_group_view(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        gid = int(call.data.split(":")[2])
        g = storage.get_group(gid)
        if not g:
            bot.edit_message_text("Класс не найден.", call.message.chat.id, call.message.message_id)
            return
        summary = storage.get_group_summary(gid)
        top = storage.get_group_top_errors(gid, 8)
        top_txt = format_error_stats(top) if top else "нет данных"

        text = (
            f"🏫 <b>{g['name']}</b>\n"
            f"Код: <code>{g['join_code']}</code>\n\n"
            f"Ученики: <b>{summary['students']}</b> | Учителя: <b>{summary['teachers']}</b>\n"
            f"Средний XP (ученики): <b>{summary['xp_avg']}</b>\n"
            f"Среднее пройдено уроков (КТП): <b>{summary['ktp_done_avg']}</b>\n\n"
            f"<b>Частые ошибки класса:</b>\n{top_txt}"
        )
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=_kb_one_col([
                ("👥 Список участников", f"admin:group_members:{gid}"),
                ("📢 Рассылка в класс", f"admin:bc_group:{gid}"),
                ("📤 Экспорт прогресса (КТП)", f"admin:export_group_progress:{gid}"),
                ("📤 Экспорт участников", f"admin:export_group:{gid}"),
                ("🏫 К списку классов", "admin:groups"),
            ]),
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("admin:group_members:"))
    def on_group_members(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        gid = int(call.data.split(":")[2])
        members = storage.get_group_members(gid)
        lines = ["👥 <b>Участники</b>\n"]
        for m in members[:40]:
            role = "👩‍🏫" if m.get("role") == "teacher" else "👤"
            lines.append(f"{role} {m.get('first_name','')} (@{m.get('username','')}) — XP {m.get('xp',0)}")
        bot.edit_message_text(
            "\n".join(lines),
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=_kb_one_col([
                ("⬅️ Назад", f"admin:group:{gid}"),
                ("🏫 К списку", "admin:groups"),
            ]),
        )

    # ── Broadcast ─────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "admin:broadcast")
    def on_broadcast(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        storage.set_progress(uid, "admin_state", "await_bc_all")
        bot.send_message(call.message.chat.id, "📢 Напиши текст рассылки <b>всем</b> пользователям.", parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("admin:bc_group:"))
    def on_bc_group(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        gid = int(call.data.split(":")[2])
        storage.set_progress(uid, "admin_state", "await_bc_group")
        storage.set_progress(uid, "admin_bc_gid", str(gid))
        bot.send_message(call.message.chat.id, "📢 Напиши текст рассылки <b>классу</b>.", parse_mode="HTML")

    @bot.message_handler(func=lambda m: is_admin(m.from_user.id) and storage.get_progress(m.from_user.id, "admin_state","").startswith("await_bc_"))
    def on_bc_text(message):
        uid = message.from_user.id
        text = (message.text or "").strip()
        state = storage.get_progress(uid, "admin_state", "")
        storage.set_progress(uid, "admin_state", "")
        if not text:
            bot.reply_to(message, "Пустой текст.")
            return
        if state == "await_bc_all":
            ids = _all_user_ids()
        else:
            gid = int(storage.get_progress(uid, "admin_bc_gid", "0") or 0)
            ids = _group_user_ids(gid)

        sent, failed = _broadcast_ids(ids, text)
        bot.reply_to(message, f"✅ Готово. Отправлено: {sent}, не удалось: {failed}")

    def _all_user_ids() -> List[int]:
        con = storage._get_con()
        cur = con.cursor()
        cur.execute("SELECT user_id FROM users")
        return [int(r[0]) for r in cur.fetchall()]

    def _group_user_ids(group_id: int) -> List[int]:
        members = storage.get_group_members(group_id)
        return [int(m["user_id"]) for m in members if m.get("user_id")]

    def _broadcast_ids(ids: List[int], text: str) -> tuple[int, int]:
        sent = 0
        failed = 0
        for uid in ids:
            try:
                bot.send_message(uid, text, parse_mode="HTML", disable_web_page_preview=True)
                sent += 1
            except Exception:
                failed += 1
        return sent, failed

    # ── Export ────────────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "admin:export")
    def on_export(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        path = _export_users_csv()
        with open(path, "rb") as f:
            bot.send_document(call.message.chat.id, f, caption="users_export.csv")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("admin:export_group:"))
    def on_export_group(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        gid = int(call.data.split(":")[2])
        g = storage.get_group(gid)
        if not g:
            bot.send_message(call.message.chat.id, "Класс не найден.")
            return
        path = _export_group_members_csv(gid, g["name"])
        with open(path, "rb") as f:
            bot.send_document(call.message.chat.id, f, caption=f"group_{g['name']}.csv")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("admin:export_group_progress:"))
    def on_export_group_progress(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        gid = int(call.data.split(":")[2])
        g = storage.get_group(gid)
        if not g:
            bot.send_message(call.message.chat.id, "Класс не найден.")
            return
        lesson_ids = [l.lesson_id for l in KTP_LESSONS]
        path = _export_group_progress_csv(gid, g["name"], lesson_ids)
        with open(path, "rb") as f:
            bot.send_document(call.message.chat.id, f, caption=f"group_progress_{g['name']}.csv")

    def _export_users_csv() -> str:
        import sqlite3 as _sqlite3
        con = storage._get_con()
        con.row_factory = _sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT user_id, first_name, username, xp, streak, language_level, last_active_ymd, mode FROM users")
        rows = [dict(r) for r in cur.fetchall()]
        con.row_factory = None
        out = os.path.join(tempfile.gettempdir(), f"users_export_{_now_ymd()}.csv")
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["user_id"])
            w.writeheader()
            for r in rows:
                w.writerow(r)
        return out

    def _export_group_members_csv(group_id: int, group_name: str) -> str:
        out = os.path.join(tempfile.gettempdir(), f"group_{group_name}_{_now_ymd()}.csv".replace(" ", "_"))
        members = storage.get_group_members(group_id)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["user_id", "first_name", "username", "role", "xp", "streak", "language_level"],
            )
            w.writeheader()
            for m in members:
                w.writerow({
                    "user_id": m.get("user_id"),
                    "first_name": m.get("first_name"),
                    "username": m.get("username"),
                    "role": m.get("role"),
                    "xp": m.get("xp", 0),
                    "streak": m.get("streak", 0),
                    "language_level": m.get("language_level"),
                })
        return out

    def _export_group_progress_csv(group_id: int, group_name: str, lesson_ids: List[str]) -> str:
        out = os.path.join(tempfile.gettempdir(), f"group_progress_{group_name}_{_now_ymd()}.csv".replace(" ", "_"))
        rows = storage.export_group_progress_rows(group_id, lesson_ids)
        # Make stable header order
        header = ["user_id", "first_name", "username", "xp", "streak", "language_level", "lessons_done"]
        for lid in lesson_ids:
            header.append(f"{lid}_done")
            header.append(f"{lid}_exam_best")
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        return out


# ── Student / Teacher join helpers ────────────────────────────────────────────
def register_join_commands(bot):
    @bot.message_handler(commands=["join"])
    def on_join(msg):

        args = (msg.text or "").split()
        if len(args) < 2:
            bot.reply_to(msg, "Используй: <code>/join ABC123</code>", parse_mode="HTML")
            return
        code = args[1].strip().upper()
        g = storage.join_group_by_code(msg.from_user.id, code, role="student")
        if not g:
            bot.reply_to(msg, "Код не найден. Проверь и попробуй снова.")
            return
        bot.reply_to(msg, f"✅ Ты присоединился к классу <b>{g['name']}</b>.", parse_mode="HTML")

    @bot.message_handler(commands=["teach"])
    def on_teach(msg):
        storage.init_db()
        args = (msg.text or "").split()
        if len(args) < 2:
            bot.reply_to(msg, "Используй: <code>/teach ABC123</code>", parse_mode="HTML")
            return
        code = args[1].strip().upper()
        g = storage.join_group_by_code(msg.from_user.id, code, role="teacher")
        if not g:
            bot.reply_to(msg, "Код не найден. Проверь и попробуй снова.")
            return
        bot.reply_to(msg, f"✅ Вы добавлены как учитель в класс <b>{g['name']}</b>.\nКоманда: /teacher", parse_mode="HTML")

    @bot.message_handler(commands=["teacher"])
    def on_teacher(msg):
        storage.init_db()
        groups = storage.get_user_groups(msg.from_user.id)
        teach_groups = [g for g in groups if g.get("role") == "teacher"]
        if not teach_groups:
            bot.reply_to(msg, "У вас нет классов учителя. Добавьтесь через /teach CODE.")
            return

        lines = ["👩‍🏫 <b>Панель учителя</b>"]
        for g in teach_groups:
            summary = storage.get_group_summary(int(g["group_id"]))
            lines.append(
                f"\n🏫 <b>{g['name']}</b>\n"
                f"Ученики: {summary['students']} | Средний XP: {summary['xp_avg']}\n"
                f"Среднее пройдено уроков (КТП): {summary['ktp_done_avg']}"
            )
        bot.send_message(msg.chat.id, "\n".join(lines), parse_mode="HTML")


# ── Admin: pre-generate KTP lesson cache ─────────────────────────────────────
def register_prewarm_command(bot):
    """Admin-only: /prewarm [N] — pre-generate and cache KTP lesson packages."""
    from ai import predefined_ktp_package, generate_ktp_package_via_ai

    @bot.message_handler(commands=["prewarm"])
    def on_prewarm(msg):
        uid = msg.from_user.id
        if not is_admin(uid):
            bot.reply_to(msg, "⛔️ Доступ запрещён.")
            return

        args = (msg.text or "").split()
        n = None
        if len(args) >= 2:
            try:
                n = max(1, min(int(args[1]), len(KTP_LESSONS)))
            except Exception:
                n = None

        lesson_list = KTP_LESSONS[:n] if n else KTP_LESSONS
        bot.send_message(msg.chat.id, f"🧠 Начинаю генерацию кеша для уроков: {len(lesson_list)} шт.")

        ok = 0
        fail = 0
        for i, l in enumerate(lesson_list, 1):
            try:
                if storage.get_ktp_cache(l.lesson_id):
                    ok += 1
                    continue
                pack = predefined_ktp_package(l.lesson_id)
                if not pack:
                    pack = generate_ktp_package_via_ai(l.lesson_id, l.title, l.lt, l.kind)
                storage.set_ktp_cache(l.lesson_id, pack)
                ok += 1
                if i % 3 == 0:
                    bot.send_message(msg.chat.id, f"…готово {i}/{len(lesson_list)}")
            except Exception as e:
                fail += 1
                bot.send_message(msg.chat.id, f"⚠️ {l.lesson_id}: не удалось — {e}")

        bot.send_message(msg.chat.id, f"✅ Готово. Успешно: {ok}, ошибок: {fail}.")
