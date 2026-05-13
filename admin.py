# admin.py — Admin + teacher panels (groups, broadcast, export, document upload)
from __future__ import annotations

import os
import csv
import tempfile
from datetime import datetime
from typing import Optional, List

from dotenv import load_dotenv
load_dotenv()

import storage
from ktp_plan import KTP_LESSONS, register_custom_lesson
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
            return
        # Delete the /admin command message to keep chat clean
        try:
            bot.delete_message(msg.chat.id, msg.message_id)
        except Exception:
            pass

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
            [("📄 Добавить упражнения", "admin:exercises")],
            [("❌ Закрыть", "admin:close")],
        )

    @bot.callback_query_handler(func=lambda c: c.data == "admin:close")
    def on_close(call):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id)
            return
        bot.answer_callback_query(call.id)
        # Delete the admin panel message entirely instead of leaving "Закрыта"
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            bot.edit_message_text("✅", call.message.chat.id, call.message.message_id)

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


    # ── Exercise management flow ────────────────────────────────────────────
    # Flow: admin:exercises → ALL topics (built-in + custom) with pagination
    #       → upload doc for any topic OR create new topic

    TOPICS_PER_PAGE = 8

    def _build_all_topics_list():
        """Build a combined list of ALL topics: built-in KTP lessons + custom topics.
        Returns list of dicts: {lesson_id, title, semester, num, is_custom, topic_id (if custom)}
        """
        topics = []
        # 1) Built-in lessons from ktp_plan
        for l in KTP_LESSONS:
            # Skip custom lessons already in KTP_LESSONS (they'll be added from DB)
            if l.lesson_id.startswith("custom_"):
                continue
            topics.append({
                "lesson_id": l.lesson_id,
                "title": l.title,
                "semester": l.semester,
                "num": l.num,
                "is_custom": False,
                "topic_id": None,
            })
        # 2) Custom topics from DB
        custom = storage.list_custom_topics()
        for t in custom:
            topics.append({
                "lesson_id": t["lesson_id"],
                "title": t["name"],
                "semester": t.get("semester", 4),
                "num": 0,
                "is_custom": True,
                "topic_id": t["topic_id"],
            })
        # Sort by semester, then by num
        topics.sort(key=lambda x: (x["semester"], x["num"], x["title"]))
        return topics

    @bot.callback_query_handler(func=lambda c: c.data == "admin:exercises")
    def on_exercises(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        _show_all_topics_page(call.message.chat.id, call.message.message_id, page=0)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("admin:all_topics_page:"))
    def on_all_topics_page(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        page = int(call.data.split(":")[2])
        _show_all_topics_page(call.message.chat.id, call.message.message_id, page)

    def _show_all_topics_page(chat_id, msg_id, page=0):
        """Show paginated list of ALL topics (built-in + custom) with exercise status."""
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        from ai import predefined_ktp_package

        all_topics = _build_all_topics_list()
        total = len(all_topics)
        start = page * TOPICS_PER_PAGE
        end = min(start + TOPICS_PER_PAGE, total)
        page_topics = all_topics[start:end]
        total_pages = (total + TOPICS_PER_PAGE - 1) // TOPICS_PER_PAGE

        kb = InlineKeyboardMarkup(row_width=1)
        for t in page_topics:
            cached = storage.get_ktp_cache(t["lesson_id"])
            predefined = predefined_ktp_package(t["lesson_id"])

            has_uploaded = cached and len(cached.get("practice", [])) > 0
            has_predefined = predefined and len(predefined.get("practice", [])) > 0

            if has_uploaded:
                icon = "✅" # Uploaded by admin
            elif has_predefined:
                icon = "📋" # System predefined
            elif not t["is_custom"]:
                icon = "🤖" # Built-in, will be AI generated
            else:
                icon = "⏳" # Custom, but no document yet

            custom_mark = "📄" if t["is_custom"] else ""
            # Truncate long titles for button text (Telegram limit ~64 chars)
            title_short = t["title"][:45] + "…" if len(t["title"]) > 45 else t["title"]
            label_text = f"{icon} С{t['semester']}"
            if t["num"]:
                label_text += f".{t['num']}"
            label_text += f" {custom_mark}{title_short}"

            cb_data = f"admin:upload_for:{t['lesson_id']}"
            kb.add(InlineKeyboardButton(label_text, callback_data=cb_data))

        # Pagination
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Назад", callback_data=f"admin:all_topics_page:{page - 1}"))
        if end < total:
            nav_row.append(InlineKeyboardButton("Вперёд ▶️", callback_data=f"admin:all_topics_page:{page + 1}"))
        if nav_row:
            kb.row(*nav_row)

        kb.add(InlineKeyboardButton("➕ Новая тема", callback_data="admin:add_new_topic"))
        kb.add(InlineKeyboardButton("⬅️ Админ-панель", callback_data="admin:menu"))

        bot.edit_message_text(
            f"📄 <b>Все темы</b> (стр. {page + 1}/{total_pages})\n\n"
            f"✅ = загружен файл, 📋 = системные\n"
            f"🤖 = ИИ-генерация, ⏳ = пусто\n"
            f"📄 = доп. тема\n\n"
            f"Нажми на тему, чтобы загрузить/обновить упражнения:",
            chat_id, msg_id,
            reply_markup=kb,
            parse_mode="HTML",
        )

    # ── Upload for any topic (built-in or custom) ─────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith("admin:upload_for:"))
    def on_upload_for(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        lesson_id = call.data.split(":", 2)[2]

        # Find topic info — could be built-in or custom
        from ktp_plan import LESSON_BY_ID as LBI
        meta = LBI.get(lesson_id)
        if meta:
            title = meta.title
            sem = meta.semester
        else:
            # Try custom topic from DB
            all_custom = storage.list_custom_topics()
            found = None
            for t in all_custom:
                if t["lesson_id"] == lesson_id:
                    found = t
                    break
            if found:
                title = found["name"]
                sem = found.get("semester", 4)
            else:
                bot.send_message(call.message.chat.id, "⚠️ Тема не найдена.")
                return

        # Show topic info + upload prompt
        cached = storage.get_ktp_cache(lesson_id)
        has_exercises = cached and len(cached.get("practice", [])) > 0
        practice_count = len(cached.get("practice", [])) if cached else 0
        exam_count = len(cached.get("exam", [])) if cached else 0

        status = "✅ Упражнения загружены" if has_exercises else "⏳ Упражнения не загружены"
        exercise_info = ""
        if has_exercises:
            exercise_info = f"\nПрактика: {practice_count} вопросов | Контрольная: {exam_count} вопросов"

        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(row_width=1)
        btn_text = "🔄 Перезагрузить документ" if has_exercises else "📎 Загрузить документ"
        kb.add(InlineKeyboardButton(btn_text, callback_data=f"admin:start_upload:{lesson_id}"))
        kb.add(InlineKeyboardButton("⬅️ К списку тем", callback_data="admin:exercises"))
        kb.add(InlineKeyboardButton("⬅️ Админ-панель", callback_data="admin:menu"))

        bot.edit_message_text(
            f"📄 <b>{title}</b>\n"
            f"Семестр: <b>{sem}</b> | ID: <code>{lesson_id}</code>\n\n"
            f"{status}{exercise_info}",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb,
            parse_mode="HTML",
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("admin:start_upload:"))
    def on_start_upload(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        lesson_id = call.data.split(":", 2)[2]

        # Find the topic title
        from ktp_plan import LESSON_BY_ID as LBI
        meta = LBI.get(lesson_id)
        if meta:
            title = meta.title
            sem = meta.semester
        else:
            all_custom = storage.list_custom_topics()
            found = None
            for t in all_custom:
                if t["lesson_id"] == lesson_id:
                    found = t
                    break
            if found:
                title = found["name"]
                sem = found.get("semester", 4)
            else:
                bot.send_message(call.message.chat.id, "⚠️ Тема не найдена.")
                return

        storage.set_progress(uid, "admin_state", "await_doc_upload")
        storage.set_progress(uid, "admin_topic_lesson_id", lesson_id)
        storage.set_progress(uid, "admin_topic_name", title)
        storage.set_progress(uid, "admin_selected_sem", str(sem))

        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("⬅️ Отмена", callback_data=f"admin:upload_for:{lesson_id}"))
        bot.send_message(
            call.message.chat.id,
            f"📎 Отправь документ с упражнениями для темы <b>{title}</b> (DOCX или PDF).\n\n"
            f"Или нажми «Отмена» для возврата.",
            parse_mode="HTML",
            reply_markup=kb,
        )

    # ── Add new topic flow ────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == "admin:add_new_topic")
    def on_add_new_topic(call):
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)
        storage.set_progress(uid, "admin_state", "await_new_topic_sem")
        bot.send_message(
            call.message.chat.id,
            "🔢 Введи номер семестра для новой темы (1, 2, 3 или другое число):",
            parse_mode="HTML",
        )

    @bot.message_handler(func=lambda m: is_admin(m.from_user.id) and storage.get_progress(m.from_user.id, "admin_state") == "await_new_topic_sem")
    def on_new_topic_sem(message):
        uid = message.from_user.id
        txt = (message.text or "").strip()
        try:
            sem = int(txt)
            if sem < 1 or sem > 99:
                raise ValueError
        except ValueError:
            bot.reply_to(message, "❌ Введи число от 1 до 99.")
            return
        storage.set_progress(uid, "admin_selected_sem", str(sem))
        storage.set_progress(uid, "admin_state", "await_topic_name")
        bot.send_message(
            message.chat.id,
            f"✅ Семестр <b>{sem}</b>.\n\n"
            f"✍️ Теперь напиши название новой темы\n"
            f"(например: <b>Причастия</b>):",
            parse_mode="HTML",
        )

    @bot.message_handler(func=lambda m: is_admin(m.from_user.id) and storage.get_progress(m.from_user.id, "admin_state") == "await_topic_name")
    def on_topic_name(message):
        uid = message.from_user.id
        name = (message.text or "").strip()
        if len(name) < 2:
            bot.reply_to(message, "Название слишком короткое. Попробуй ещё раз.")
            return
        sem = int(storage.get_progress(uid, "admin_selected_sem", "4") or 4)
        try:
            topic = storage.create_custom_topic(name, semester=sem)
        except Exception as e:
            bot.reply_to(message, f"⚠️ Ошибка: {e}")
            storage.set_progress(uid, "admin_state", "")
            return
        # Register as KTP lesson
        register_custom_lesson(topic["lesson_id"], topic["name"], semester=sem)
        storage.set_progress(uid, "admin_state", "await_doc_upload")
        storage.set_progress(uid, "admin_topic_lesson_id", topic["lesson_id"])
        storage.set_progress(uid, "admin_topic_name", topic["name"])
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("📄 К списку тем", callback_data="admin:exercises"))
        kb.add(InlineKeyboardButton("⬅️ Админ-панель", callback_data="admin:menu"))
        bot.send_message(
            message.chat.id,
            f"✅ Тема <b>{topic['name']}</b> создана в семестре <b>{sem}</b>!\n\n"
            f"📎 Теперь отправь документ с упражнениями (DOCX или PDF).\n\n"
            f"Или нажми кнопку, чтобы вернуться:",
            parse_mode="HTML",
            reply_markup=kb,
        )

    # ── Document upload handler ───────────────────────────────────────────

    @bot.message_handler(
        content_types=["document"],
        func=lambda m: is_admin(m.from_user.id) and storage.get_progress(m.from_user.id, "admin_state") == "await_doc_upload",
    )
    def on_doc_upload(message):
        uid = message.from_user.id
        lesson_id = storage.get_progress(uid, "admin_topic_lesson_id", "")
        topic_name = storage.get_progress(uid, "admin_topic_name", "")
        sem = int(storage.get_progress(uid, "admin_selected_sem", "4") or 4)
        storage.set_progress(uid, "admin_state", "")

        if not lesson_id or not topic_name:
            bot.reply_to(message, "⚠️ Ошибка: тема не выбрана. Начни заново через /admin.")
            return

        doc = message.document
        file_name = doc.file_name or "file.docx"
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in (".docx", ".pdf"):
            bot.reply_to(message, "⚠️ Поддерживаются только DOCX и PDF файлы.")
            return

        bot.send_message(message.chat.id, f"📥 Получен файл: <b>{file_name}</b>\n⏳ Обрабатываю...", parse_mode="HTML")

        try:
            from doc_parser import save_telegram_file, extract_text
            from ai import parse_exercises_from_document

            file_info = bot.get_file(doc.file_id)
            file_path = save_telegram_file(bot, file_info, file_name)

            # Extract text
            text = extract_text(file_path)
            if not text or len(text.strip()) < 20:
                bot.send_message(message.chat.id, "⚠️ Документ пуст или слишком короткий.")
                os.unlink(file_path)
                return

            bot.send_message(message.chat.id, f"📝 Извлечено {len(text)} символов.\n🤖 Генерирую упражнения через ИИ...")

            # Parse via AI
            package = parse_exercises_from_document(text, topic_name)

            # Save to cache — works for BOTH built-in and custom lesson IDs
            storage.set_ktp_cache(lesson_id, package)

            # Register lesson if custom (built-in ones are already registered)
            if lesson_id.startswith("custom_"):
                register_custom_lesson(lesson_id, topic_name, semester=sem)

            practice_count = len(package.get("practice", []))
            exam_count = len(package.get("exam", []))
            vocab_count = len(package.get("vocab", []))

            # Navigation buttons after completion
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("📄 К списку тем", callback_data="admin:exercises"))
            kb.add(InlineKeyboardButton("🛠 Админ-панель", callback_data="admin:menu"))

            bot.send_message(
                message.chat.id,
                f"✅ <b>Упражнения готовы!</b>\n\n"
                f"📄 Тема: <b>{topic_name}</b>\n"
                f"📘 Семестр: <b>{sem}</b>\n"
                f"🧩 Практика: <b>{practice_count}</b> вопросов\n"
                f"📝 Контрольная: <b>{exam_count}</b> вопросов\n"
                f"🃏 Словарик: <b>{vocab_count}</b> слов\n\n"
                f"Упражнения доступны в КТП → Семестр {sem} → <b>{topic_name}</b>",
                parse_mode="HTML",
                reply_markup=kb,
            )

            # Cleanup temp file
            os.unlink(file_path)

        except Exception as e:
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("🛠 Админ-панель", callback_data="admin:menu"))
            bot.send_message(message.chat.id, f"⚠️ Ошибка при обработке: {e}", reply_markup=kb)


# ── Student / Teacher join helpers ────────────────────────────────────────────
def register_join_commands(bot):
    @bot.message_handler(commands=["join"])
    def on_join(msg):
        # Delete command message to keep chat clean
        try:
            bot.delete_message(msg.chat.id, msg.message_id)
        except Exception:
            pass

        args = (msg.text or "").split()
        if len(args) < 2:
            bot.reply_to(msg, "Используй: <code>/join ABC123</code>", parse_mode="HTML")
            return
        code = args[1].strip().upper()
        g = storage.join_group_by_code(msg.from_user.id, code, role="student")
        if not g:
            bot.reply_to(msg, "Код не найден. Проверь и попробуй снова.")
            return
        bot.send_message(msg.chat.id, f"✅ Ты присоединился к классу <b>{g['name']}</b>.", parse_mode="HTML")

    @bot.message_handler(commands=["teach"])
    def on_teach(msg):
        storage.init_db()
        # Delete command message to keep chat clean
        try:
            bot.delete_message(msg.chat.id, msg.message_id)
        except Exception:
            pass

        args = (msg.text or "").split()
        if len(args) < 2:
            bot.send_message(msg.chat.id, "Используй: <code>/teach ABC123</code>", parse_mode="HTML")
            return
        code = args[1].strip().upper()
        g = storage.join_group_by_code(msg.from_user.id, code, role="teacher")
        if not g:
            bot.send_message(msg.chat.id, "Код не найден. Проверь и попробуй снова.")
            return
        bot.send_message(msg.chat.id, f"✅ Вы добавлены как учитель в класс <b>{g['name']}</b>.\nКоманда: /teacher", parse_mode="HTML")

    @bot.message_handler(commands=["teacher"])
    def on_teacher(msg):
        storage.init_db()
        # Delete command message to keep chat clean
        try:
            bot.delete_message(msg.chat.id, msg.message_id)
        except Exception:
            pass

        groups = storage.get_user_groups(msg.from_user.id)
        teach_groups = [g for g in groups if g.get("role") == "teacher"]
        if not teach_groups:
            bot.send_message(msg.chat.id, "У вас нет классов учителя. Добавьтесь через /teach CODE.")
            return

        lines = ["👩\u200d🏫 <b>Панель учителя</b>"]
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
