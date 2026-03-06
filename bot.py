# bot.py — Dual-mode Russian Language Tutor v3
import os, json
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import storage, class_handlers
import admin
import ktp_handlers
from ktp_plan import TAG_TO_RECOMMEND, LESSON_BY_ID
from content import TEST_QUESTIONS, MODULES, MODULE_ORDER, ACHIEVEMENTS
from ai import evaluate_morphology_writing
from utils import format_tags, format_error_stats, label, TAG_LABELS

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in .env — bot cannot start.")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
from admin import ADMIN_IDS  # single source of truth
class_handlers.register(bot)   # legacy class-mode handlers (optional)
ktp_handlers.register(bot)      # KTP curriculum handlers
admin.register_join_commands(bot)
admin.register(bot)
admin.register_prewarm_command(bot)

CTRL_THRESHOLD = 5   # out of 8 for self-study
XP_CTRL = 30
HINT_COST = 2

HINTS = class_handlers.HINTS
DEFAULT_HINT = class_handlers.DEFAULT_HINT

# ── Helpers ──────────────────────────────────────────────────────────────────
def today_ymd(): return datetime.now().strftime("%Y-%m-%d")
def log(uid, ev, p): storage.log_event(uid, ev, json.dumps(p, ensure_ascii=False))
def safe_name(u): return (u.first_name or "").strip() or "друг"

def lang_level_from_score(s):
    return "A1" if s <= 3 else ("A2" if s <= 7 else "B1")

def rank_from_xp(xp):
    if xp < 100:  return "🌱 Новичок"
    if xp < 300:  return "🔍 Исследователь"
    if xp < 600:  return "🧠 Знаток"
    return "🌍 Посол культуры"

def maybe_unlock(uid, code):
    if storage.unlock_achievement(uid, code):
        m = ACHIEVEMENTS.get(code)
        if m: bot.send_message(uid, f"{m['emoji']} <b>Достижение:</b> {m['title']}\n{m['desc']}")

def count_completed_levels(uid):
    return sum(1 for mid in MODULE_ORDER for lvl in [1,2,3]
               if storage.is_level_ctrl_passed(uid, mid, lvl))

def update_meta(uid):
    u = storage.get_user(uid) or {}
    xp = int(u.get("xp", 0) or 0)
    streak = int(u.get("streak", 0) or 0)
    if streak >= 3: maybe_unlock(uid, "streak_3")
    if xp >= 100: maybe_unlock(uid, "xp_100")
    if xp >= 300: maybe_unlock(uid, "xp_300")
    done = count_completed_levels(uid)
    if done >= 1: maybe_unlock(uid, "first_lesson")
    if done >= 3: maybe_unlock(uid, "three_lessons")
    if done >= 9: maybe_unlock(uid, "all_lessons")
    for mid in MODULE_ORDER:
        if all(storage.is_level_ctrl_passed(uid, mid, l) for l in [1,2,3]):
            maybe_unlock(uid, f"module_{mid}")

def lv_icon(uid, mid, lvl):
    if storage.is_level_ctrl_passed(uid, mid, lvl): return "✅"
    return "🔓" if lvl <= storage.get_module_level_unlocked(uid, mid) else "🔒"

# ── Keyboards ────────────────────────────────────────────────────────────────
def kb_main():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("📘 Учебный план (КТП)", callback_data="menu:ktp"),
           InlineKeyboardButton("🧪 Диагностика", callback_data="menu:test"))
    kb.add(InlineKeyboardButton("🧠 Тренажёры", callback_data="menu:home"),
           InlineKeyboardButton("🏫 Мой класс", callback_data="menu:class"))
    kb.add(InlineKeyboardButton("🏆 Лидеры", callback_data="menu:leaders"),
           InlineKeyboardButton("👤 Профиль", callback_data="menu:profile"))
    kb.add(InlineKeyboardButton("ℹ️ Помощь", callback_data="menu:help"))
    return kb


def kb_home_modules(uid):
    kb = InlineKeyboardMarkup()
    for mid in MODULE_ORDER:
        m = MODULES[mid]
        done = sum(1 for l in [1,2,3] if storage.is_level_ctrl_passed(uid, mid, l))
        kb.add(InlineKeyboardButton(f"{m['emoji']} {m['title']} ({done}/3)",
                                    callback_data=f"module:{mid}"))
    kb.add(InlineKeyboardButton("📊 Мои ошибки", callback_data="home:errors"))
    kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))
    return kb

def kb_levels(uid, mid):
    mod = MODULES[mid]
    kb = InlineKeyboardMarkup()
    for lvl in [1,2,3]:
        icon = lv_icon(uid, mid, lvl)
        kb.add(InlineKeyboardButton(f"{icon} {mod['levels'][lvl]['title']}",
                                    callback_data=f"level:{mid}:{lvl}"))
    kb.add(InlineKeyboardButton(f"🃏 Карточки", callback_data=f"flash:{mid}"))
    kb.add(InlineKeyboardButton("⬅️ К модулям", callback_data="menu:home"))
    return kb

# ── /start ───────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def on_start(msg):
    storage.upsert_user(msg.from_user.id, msg.from_user.first_name, msg.from_user.username)
    maybe_unlock(msg.from_user.id, "first_start")
    streak, changed = storage.update_streak(msg.from_user.id, today_ymd())
    if changed: storage.add_xp(msg.from_user.id, 5)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📖 В классе", callback_data="mode:class"),
           InlineKeyboardButton("🏠 Самостоятельно", callback_data="mode:home"))
    bot.send_message(msg.chat.id,
        f"👋 Привет, <b>{safe_name(msg.from_user)}</b>! 🇺🇿→🇷🇺\n\n"
        "Я учу тебя <b>морфологии русского языка</b>.\n\n"
        "📍 <b>Как ты занимаешься прямо сейчас?</b>", reply_markup=kb)


@bot.message_handler(commands=["id"])
def on_id(msg):
    bot.reply_to(msg, f"🆔 Твой Telegram ID: <code>{msg.from_user.id}</code>", parse_mode="HTML")

@bot.callback_query_handler(func=lambda c: c.data.startswith("mode:"))
def on_mode(call):
    bot.answer_callback_query(call.id)
    mode = call.data.split(":")[1]
    storage.set_user_mode(call.from_user.id, mode)
    label = "📖 <b>Режим: В классе</b>\n💡 Уроки открываются по расписанию." \
            if mode == "class" else \
            "🏠 <b>Режим: Самостоятельно</b>\n💡 Все модули открыты — учись в своём темпе."
    bot.edit_message_text(f"✅ {label}\n\n<b>Выбери действие:</b>",
        call.message.chat.id, call.message.message_id, reply_markup=kb_main())

# ── Main menu ────────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("menu:") and c.data not in ("menu:ktp","menu:leaders"))
def on_menu(call):
    storage.upsert_user(call.from_user.id, call.from_user.first_name, call.from_user.username)
    action = call.data.split(":", 1)[1]
    uid = call.from_user.id

    if action == "test":
        start_test(call)
    elif action == "home":
        bot.answer_callback_query(call.id)
        bot.edit_message_text("📚 <b>Самостоятельное обучение</b>\n\nВыбери модуль:",
            call.message.chat.id, call.message.message_id, reply_markup=kb_home_modules(uid))
    elif action == "class":
        bot.answer_callback_query(call.id)
        groups = storage.get_user_groups(uid)
        kb = InlineKeyboardMarkup(row_width=1)

        if not groups:
            text = (
                "🏫 <b>Мой класс</b>\n\n"
                "Чтобы подключиться к классу, введи команду:\n"
                "<code>/join CODE</code>\n\n"
                "Если ты учитель:\n"
                "<code>/teach CODE</code>\n\n"
                "Пока можешь учиться по учебному плану (КТП)."
            )
            kb.add(InlineKeyboardButton("📘 Открыть КТП", callback_data="menu:ktp"))
            kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
            return

        for g in groups[:10]:
            role = g.get("role", "student")
            icon = "👩‍🏫" if role == "teacher" else "👤"
            kb.add(InlineKeyboardButton(f"{icon} {g['name']}", callback_data=f"classhub:{g['group_id']}"))

        kb.add(InlineKeyboardButton("📘 Открыть КТП", callback_data="menu:ktp"))
        kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))
        bot.edit_message_text(
            "🏫 <b>Мой класс</b>\n\nВыбери класс:",
            call.message.chat.id, call.message.message_id, reply_markup=kb
        )

    elif action == "profile":
        bot.answer_callback_query(call.id)
        show_profile(call.message.chat.id, call.message.message_id, uid)
    elif action == "help":
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "ℹ️ <b>Как работает бот</b>\n\n"
            "📘 <b>Учебный план (КТП)</b> — уроки по программе: теория → практика → мини‑контрольная → письмо с ИИ.\n"
            "  Все уроки доступны сразу.\n\n"
            "🧠 <b>Тренажёры</b> — дополнительные модули морфологии (3 уровня).\n\n"
            "🏫 <b>Мой класс</b> — подключение к группе: /join CODE (ученик) · /teach CODE (учитель).\n\n"
            "✍️ <b>ИИ проверяет</b>: грамматику, орфографию, связность/логику и лексику.\n"
            "💡 <b>Подсказки</b> — доступны в каждом вопросе (−2 XP).\n"
            "🏆 <b>Ранги</b> — растут по XP.",
            call.message.chat.id, call.message.message_id, reply_markup=kb_main())

@bot.callback_query_handler(func=lambda c: c.data == "nav:menu")
def on_nav_menu(call):
    bot.answer_callback_query(call.id)
    bot.edit_message_text("Выбирай действие:", call.message.chat.id,
                          call.message.message_id, reply_markup=kb_main())


# ── Leaders / class hub ──────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data == "menu:leaders")
def on_leaders(call):
    bot.answer_callback_query(call.id)
    top = storage.get_top_users(10)
    lines = ["🏆 <b>Лидеры по XP</b>\n"]
    for i, u in enumerate(top, 1):
        name = (u.get("first_name") or "—").strip()
        uname = f"@{u.get('username')}" if u.get("username") else ""
        lines.append(f"{i}. <b>{name}</b> {uname} — <b>{u.get('xp',0)}</b> XP")
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📘 Учебный план (КТП)", callback_data="menu:ktp"))
    kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))
    bot.edit_message_text("\n".join(lines), call.message.chat.id, call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("classhub:") and c.data.count(":")==1)
def on_classhub(call):
    bot.answer_callback_query(call.id)
    gid = int(call.data.split(":")[1])
    g = storage.get_group(gid)
    if not g:
        bot.answer_callback_query(call.id, "Класс не найден.", show_alert=True)
        return
    summary = storage.get_group_summary(gid)
    top_err = storage.get_group_top_errors(gid, 6)
    err_txt = format_error_stats(top_err) if top_err else "нет данных"
    leaders = storage.get_group_leaderboard(gid, 5)
    lead_lines = []
    for i, u in enumerate(leaders, 1):
        lead_lines.append(f"{i}. {u.get('first_name','—')} — {u.get('xp',0)} XP")
    lead_txt = "\n".join(lead_lines) if lead_lines else "—"

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("📘 Уроки (КТП)", callback_data="menu:ktp"))
    kb.add(InlineKeyboardButton("🏆 Рейтинг класса", callback_data=f"classhub:leaders:{gid}"))
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="menu:class"))
    bot.edit_message_text(
        f"🏫 <b>{g['name']}</b>\n\n"
        f"Ученики: <b>{summary['students']}</b> · Учителя: <b>{summary['teachers']}</b>\n"
        f"Средний XP: <b>{summary['xp_avg']}</b>\n"
        f"Среднее пройдено уроков (КТП): <b>{summary['ktp_done_avg']}</b>\n\n"
        f"<b>Топ ошибки класса:</b>\n{err_txt}\n\n"
        f"<b>Топ XP:</b>\n{lead_txt}",
        call.message.chat.id, call.message.message_id, reply_markup=kb
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("classhub:leaders:"))
def on_classhub_leaders(call):
    bot.answer_callback_query(call.id)
    gid = int(call.data.split(":")[2])
    leaders = storage.get_group_leaderboard(gid, 20)
    lines = ["🏆 <b>Рейтинг класса</b>\n"]
    for i, u in enumerate(leaders, 1):
        lines.append(f"{i}. <b>{u.get('first_name','—')}</b> — {u.get('xp',0)} XP")
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("⬅️ К классу", callback_data=f"classhub:{gid}"))
    bot.edit_message_text("\n".join(lines), call.message.chat.id, call.message.message_id, reply_markup=kb)

# ── Profile ───────────────────────────────────────────────────────────────────
def show_profile(chat_id, msg_id, uid):
    u = storage.get_user(uid)
    if not u:
        bot.edit_message_text("Нажми /start", chat_id, msg_id)
        return
    xp = int(u.get("xp", 0) or 0)
    streak = int(u.get("streak", 0) or 0)
    mode = u.get("mode", "home")
    mode_lbl = "📖 В классе" if mode == "class" else "🏠 Самостоятельно"

    mod_lines = []
    for mid in MODULE_ORDER:
        m = MODULES[mid]
        icons = "".join(lv_icon(uid, mid, l) for l in [1,2,3])
        mod_lines.append(f"{m['emoji']} {m['title']}: {icons}")

    from class_handlers import CLASS_SCHEDULE
    cls_done = sum(1 for l in CLASS_SCHEDULE if storage.is_class_lesson_done(uid, l["date"]))
    top_err = storage.get_top_errors(uid, 3)
    err_txt = format_error_stats(top_err) if top_err else "нет данных"
    # flatten to single line for profile
    err_short = " · ".join(f"{label(t)}({c})" for t,c in top_err) if top_err else "нет данных"
    ach = storage.get_achievements(uid)
    recent = [f"{ACHIEVEMENTS[c]['emoji']} {ACHIEVEMENTS[c]['title']}"
              for c in ach[-3:] if c in ACHIEVEMENTS]

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔄 Сменить режим", callback_data="profile:switch_mode"))
    kb.add(InlineKeyboardButton("📚 К модулям", callback_data="menu:home"),
           InlineKeyboardButton("📅 К урокам", callback_data="menu:class"))
    kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))

    bot.edit_message_text(
        f"👤 <b>Профиль</b>\n\n"
        f"Ранг: <b>{rank_from_xp(xp)}</b> | XP: <b>{xp}</b>\n"
        f"Стрик: <b>{streak}</b> дней | Уровень: <b>{u.get('language_level','—')}</b>\n"
        f"Режим: {mode_lbl}\n\n"
        f"<b>Прогресс (самост.):</b>\n" + "\n".join(mod_lines) + "\n\n"
        f"<b>Уроки в классе:</b> {cls_done}/{len(CLASS_SCHEDULE)} ✅\n\n"
        f"<b>Частые ошибки:</b> {err_short}\n"
        f"<b>Достижения:</b> {', '.join(recent) or 'пока нет'}",
        chat_id, msg_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "profile:switch_mode")
def on_switch_mode(call):
    bot.answer_callback_query(call.id)
    u = storage.get_user(call.from_user.id) or {}
    cur = u.get("mode", "home")
    new_mode = "class" if cur == "home" else "home"
    storage.set_user_mode(call.from_user.id, new_mode)
    lbl = "📖 В классе" if new_mode == "class" else "🏠 Самостоятельно"
    bot.answer_callback_query(call.id, f"Режим изменён: {lbl}", show_alert=True)
    show_profile(call.message.chat.id, call.message.message_id, call.from_user.id)

# ── Flashcards ────────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("flash:"))
def on_flashcards(call):
    bot.answer_callback_query(call.id)
    mid = call.data.split(":")[1]
    if mid not in MODULES:
        return
    mod = MODULES[mid]
    # Build flashcard text from all levels' vocab
    lines = []
    for lvl in [1, 2, 3]:
        lev = mod["levels"][lvl]
        if lev.get("vocab"):
            lines.append(f"<b>Уровень {lvl}:</b>")
            for pair in lev["vocab"]:
                lines.append(f"  📌 <b>{pair[0]}</b> — <i>{pair[1]}</i>")
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📚 К уровням", callback_data=f"module:{mid}"))
    kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))
    bot.edit_message_text(
        f"🃏 <b>Карточки: {mod['emoji']} {mod['title']}</b>\n\n" + "\n".join(lines),
        call.message.chat.id, call.message.message_id, reply_markup=kb)

# ── My errors ─────────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data == "home:errors")
def on_my_errors(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    errors = storage.get_top_errors(uid, 8)

    # Build recommendations once
    rec = []
    if errors:
        lines = []
        for t, c in errors:
            if t == "no_error":
                continue
            icon = "🔴" if c >= 5 else ("🟡" if c >= 2 else "🟢")
            lines.append(f"{icon} <b>{label(t)}</b>: {c} раз")
            if len(rec) < 3:
                for lid in TAG_TO_RECOMMEND.get(t, []):
                    if lid not in rec:
                        rec.append(lid)
        rec = rec[:3]

        text = "📊 <b>Мои частые ошибки</b>\n\n" + "\n".join(lines)
        if rec:
            text += "\n\n💡 <b>Что повторить:</b>\n" + "\n".join(
                f"• {LESSON_BY_ID[lid].title}" for lid in rec if lid in LESSON_BY_ID
            )
    else:
        text = "📊 <b>Мои ошибки</b>\n\n✅ Пока данных нет — выполняй письменные задания!"

    kb = InlineKeyboardMarkup(row_width=1)
    for lid in rec:
        if lid in LESSON_BY_ID:
            kb.add(InlineKeyboardButton(f"🔁 Повторить: {LESSON_BY_ID[lid].title[:32]}…", callback_data=f"ktp:lesson:{lid}"))

    kb.add(InlineKeyboardButton("📘 Учебный план (КТП)", callback_data="menu:ktp"))
    kb.add(InlineKeyboardButton("🧠 Тренажёры", callback_data="menu:home"))
    kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)

# ── Diagnostic test ───────────────────────────────────────────────────────────
def start_test(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    storage.clear_progress_prefix(uid, "test_")
    storage.set_progress(uid, "mode", "test")
    storage.set_progress(uid, "test_idx", "0")
    storage.set_progress(uid, "test_score", "0")
    _send_test_q(call.message.chat.id, call.message.message_id, uid)

def _send_test_q(chat_id, msg_id, uid):
    idx = int(storage.get_progress(uid, "test_idx", "0"))
    if idx >= len(TEST_QUESTIONS):
        _finish_test(chat_id, msg_id, uid); return
    q = TEST_QUESTIONS[idx]
    kb = InlineKeyboardMarkup(row_width=2)
    for i, o in enumerate(q["options"]):
        kb.add(InlineKeyboardButton(o, callback_data=f"testans:{i}"))
    kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))
    bot.edit_message_text(f"🧪 <b>Тест</b> ({idx+1}/{len(TEST_QUESTIONS)})\n\n{q['q']}",
                          chat_id, msg_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("testans:"))
def on_test_ans(call):
    uid = call.from_user.id
    if storage.get_progress(uid, "mode") != "test":
        bot.answer_callback_query(call.id, "Тест уже завершён."); return
    idx = int(storage.get_progress(uid, "test_idx", "0"))
    if idx >= len(TEST_QUESTIONS):
        bot.answer_callback_query(call.id); return
    chosen = int(call.data.split(":")[1])
    q = TEST_QUESTIONS[idx]
    ok = chosen == q["correct"]
    sc = int(storage.get_progress(uid, "test_score", "0"))
    if ok: sc += 1; storage.add_xp(uid, 2)
    storage.set_progress(uid, "test_score", str(sc))
    storage.set_progress(uid, "test_idx", str(idx+1))
    bot.answer_callback_query(call.id, "✅ Верно!" if ok else "❌ Не совсем.")
    _send_test_q(call.message.chat.id, call.message.message_id, uid)

def _finish_test(chat_id, msg_id, uid):
    sc = int(storage.get_progress(uid, "test_score", "0"))
    lv = lang_level_from_score(sc)
    storage.set_language_level(uid, lv)
    storage.set_progress(uid, "mode", "idle")
    maybe_unlock(uid, "first_test"); update_meta(uid)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📚 К модулям", callback_data="menu:home"))
    kb.add(InlineKeyboardButton("📅 К урокам", callback_data="menu:class"))
    kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))
    bot.edit_message_text(
        f"✅ <b>Тест завершён!</b>\nРезультат: <b>{sc}/{len(TEST_QUESTIONS)}</b>\n"
        f"Уровень: <b>{lv}</b>\n\nВыбери, как продолжить:",
        chat_id, msg_id, reply_markup=kb)

# ── Home: module & level navigation ──────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("module:") and len(c.data.split(":"))==2)
def on_module(call):
    bot.answer_callback_query(call.id)
    mid = call.data.split(":")[1]
    if mid not in MODULES: return
    m = MODULES[mid]
    bot.edit_message_text(f"{m['emoji']} <b>{m['title']}</b>\n\nВыбери уровень:",
        call.message.chat.id, call.message.message_id, reply_markup=kb_levels(call.from_user.id, mid))

@bot.callback_query_handler(func=lambda c: c.data.startswith("level:") and len(c.data.split(":"))==3)
def on_level(call):
    bot.answer_callback_query(call.id)
    _, mid, lvl_s = call.data.split(":")
    lvl = int(lvl_s)
    if mid not in MODULES: return
    uid = call.from_user.id
    unlocked = storage.get_module_level_unlocked(uid, mid)
    if lvl > unlocked:
        bot.answer_callback_query(call.id,
            f"🔒 Сначала сдай контрольную за Ур.{lvl-1}.", show_alert=True); return
    mod = MODULES[mid]; lev = mod["levels"][lvl]
    tasks_done = storage.is_level_tasks_done(uid, mid, lvl)
    ctrl_passed = storage.is_level_ctrl_passed(uid, mid, lvl)
    kb = InlineKeyboardMarkup()
    label = "🔁 Повторить задания" if ctrl_passed else "▶️ Начать задания"
    kb.add(InlineKeyboardButton(label, callback_data=f"tasks:start:{mid}:{lvl}"))
    if tasks_done and not ctrl_passed:
        kb.add(InlineKeyboardButton("📝 Контрольная", callback_data=f"ctrl:start:{mid}:{lvl}"))
    if lev.get("open_task_prompt") and not ctrl_passed and tasks_done:
        kb.add(InlineKeyboardButton("✍️ Письменное (ИИ)", callback_data=f"open:start:{mid}:{lvl}"))
    kb.add(InlineKeyboardButton("🃏 Карточки", callback_data=f"flash:{mid}"))
    kb.add(InlineKeyboardButton(f"⬅️ {mod['emoji']} Модуль", callback_data=f"module:{mid}"))
    status = "✅ Контрольная сдана!\n" if ctrl_passed else ("📝 Задания есть — можно на контрольную!\n" if tasks_done else "")
    bot.edit_message_text(
        f"{mod['emoji']} <b>{lev['title']}</b>\n{status}\n{lev['theory']}",
        call.message.chat.id, call.message.message_id, reply_markup=kb)

# ── Home: tasks ───────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("tasks:start:"))
def on_tasks_start(call):
    bot.answer_callback_query(call.id)
    parts = call.data.split(":")
    mid, lvl = parts[2], int(parts[3])
    uid = call.from_user.id
    storage.clear_progress_prefix(uid, f"task_state_{mid}_{lvl}")
    storage.set_progress(uid, "mode", "lesson")
    storage.set_progress(uid, "lesson_mod", mid)
    storage.set_progress(uid, "lesson_lvl", str(lvl))
    storage.set_progress(uid, "lesson_task_idx", "0")
    storage.set_progress(uid, "lesson_correct", "0")
    _send_task(call.message.chat.id, call.message.message_id, uid, mid, lvl)

def _send_task(chat_id, msg_id, uid, mid, lvl):
    tasks = MODULES[mid]["levels"][lvl]["tasks"]
    idx = int(storage.get_progress(uid, "lesson_task_idx", "0"))
    if idx >= len(tasks):
        _finish_tasks(chat_id, msg_id, uid, mid, lvl); return
    task = tasks[idx]
    kb = InlineKeyboardMarkup(row_width=1)
    for i, o in enumerate(task["options"]):
        kb.add(InlineKeyboardButton(o, callback_data=f"taskans:{mid}:{lvl}:{idx}:{i}"))
    kb.add(InlineKeyboardButton(f"💡 Подсказка (−{HINT_COST} XP)", callback_data=f"hint:{mid}:{lvl}:{idx}"))
    kb.add(InlineKeyboardButton(f"⬅️ К уровню", callback_data=f"level:{mid}:{lvl}"))
    bot.edit_message_text(
        f"🧩 <b>Задание</b> ({idx+1}/{len(tasks)})\n\n{task['q']}",
        chat_id, msg_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("hint:"))
def on_hint(call):
    parts = call.data.split(":")
    mid, lvl, idx = parts[1], int(parts[2]), int(parts[3])
    uid = call.from_user.id
    # Check XP balance before deducting
    current_xp = storage.get_user_xp(uid)
    if current_xp < HINT_COST:
        bot.answer_callback_query(call.id, "❌ Недостаточно XP для подсказки.", show_alert=True)
        return
    task = MODULES[mid]["levels"][lvl]["tasks"][idx]
    tag = task.get("tag", "")
    hint = HINTS.get(tag, DEFAULT_HINT)
    storage.add_xp(uid, -HINT_COST)
    storage.increment_hint_used(uid)
    bot.answer_callback_query(call.id, hint, show_alert=True)

@bot.callback_query_handler(func=lambda c: c.data.startswith("taskans:"))
def on_task_ans(call):
    uid = call.from_user.id
    parts = call.data.split(":")
    mid, lvl, tidx, chosen = parts[1], int(parts[2]), int(parts[3]), int(parts[4])
    cur = int(storage.get_progress(uid, "lesson_task_idx", "0"))
    if cur != tidx:
        bot.answer_callback_query(call.id, "Продолжай по порядку."); return
    task = MODULES[mid]["levels"][lvl]["tasks"][tidx]
    ok = chosen == task["correct"]
    if ok:
        storage.add_xp(uid, task.get("xp", {1:5,2:10,3:15}[lvl]))
        c = int(storage.get_progress(uid, "lesson_correct", "0")) + 1
        storage.set_progress(uid, "lesson_correct", str(c))
    storage.set_progress(uid, "lesson_task_idx", str(tidx+1))
    bot.answer_callback_query(call.id, "✅ Верно!" if ok else f"❌ Правильно: {task['options'][task['correct']]}")
    log(uid, "task_ans", {"mod": mid, "lvl": lvl, "ok": ok, "tag": task["tag"]})
    _send_task(call.message.chat.id, call.message.message_id, uid, mid, lvl)

def _finish_tasks(chat_id, msg_id, uid, mid, lvl):
    tasks = MODULES[mid]["levels"][lvl]["tasks"]
    correct = int(storage.get_progress(uid, "lesson_correct", "0"))
    storage.mark_level_tasks_done(uid, mid, lvl)
    storage.set_progress(uid, "mode", "idle")
    ctrl_passed = storage.is_level_ctrl_passed(uid, mid, lvl)
    has_open = bool(MODULES[mid]["levels"][lvl].get("open_task_prompt"))
    kb = InlineKeyboardMarkup()
    if has_open and not ctrl_passed:
        kb.add(InlineKeyboardButton("✍️ Письменное задание (ИИ)", callback_data=f"open:start:{mid}:{lvl}"))
    if not ctrl_passed:
        kb.add(InlineKeyboardButton("📝 Контрольная работа", callback_data=f"ctrl:start:{mid}:{lvl}"))
    kb.add(InlineKeyboardButton(f"⬅️ К уровню", callback_data=f"level:{mid}:{lvl}"))
    pct = int(correct/len(tasks)*100)
    grade = "🌟" if pct>=90 else ("👍" if pct>=70 else ("😊" if pct>=50 else "💪"))
    bot.edit_message_text(
        f"🎉 <b>Задания выполнены!</b>\n\n"
        f"Правильных: <b>{correct}/{len(tasks)}</b> {grade}\n\n"
        + ("📝 Теперь сдай <b>контрольную</b>!" if not ctrl_passed else "✅ Контрольная уже сдана!"),
        chat_id, msg_id, reply_markup=kb)

# ── Home: open text (AI) ──────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("open:start:"))
def on_open_start(call):
    bot.answer_callback_query(call.id)
    parts = call.data.split(":")
    mid, lvl = parts[2], int(parts[3])
    uid = call.from_user.id
    prompt = MODULES[mid]["levels"][lvl].get("open_task_prompt")
    if not prompt: return
    storage.set_progress(uid, "mode", "awaiting_open_text")
    storage.set_progress(uid, "open_mod", mid)
    storage.set_progress(uid, "open_lvl", str(lvl))
    bot.send_message(call.message.chat.id,
        f"✍️ <b>Письменное задание</b>\n\n{prompt}\n\n<i>Напиши ответ одним сообщением.</i>")

@bot.message_handler(func=lambda m: storage.get_progress(m.from_user.id, "mode","") == "awaiting_open_text")
def on_open_text(msg):
    uid = msg.from_user.id
    text = (msg.text or "").strip()
    mid = storage.get_progress(uid, "open_mod", "")
    lvl = int(storage.get_progress(uid, "open_lvl", "1"))
    if not text or mid not in MODULES:
        storage.set_progress(uid, "mode", "idle")
        bot.reply_to(msg, "Напиши текст."); return
    u = storage.get_user(uid) or {}
    bot.send_message(msg.chat.id, "🤖 Проверяю текст...")
    try:
        result = evaluate_morphology_writing(text, u.get("language_level","A1"), MODULES[mid]["title"], lvl)
    except Exception as e:
        storage.set_progress(uid, "mode", "idle")
        bot.send_message(msg.chat.id, f"⚠️ Ошибка ИИ: {e}"); return
    storage.set_progress(uid, "mode", "idle")
    tags = result.get("error_tags", [])
    storage.track_errors(uid, tags)
    xp = 10 + result.get("score", 1) * 2
    storage.add_xp(uid, xp)
    if "no_error" in tags: maybe_unlock(uid, "no_spelling")
    update_meta(uid)
    scores = result.get("scores", {})
    score_line = (
        f"📊 грамматика {scores.get('grammar','?')}/5 · орфография {scores.get('spelling','?')}/5 · "
        f"связность {scores.get('coherence','?')}/5 · лексика {scores.get('vocabulary','?')}/5 · "
        f"<b>итог</b> {scores.get('overall', result.get('score',1))}/5"
    )
    exps = result.get("explanations", [])
    if not isinstance(exps, list): exps = [str(exps)]
    exp_txt = "\n".join(f"• {x}" for x in exps[:3]) or "• Ошибок нет!"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📝 К контрольной", callback_data=f"ctrl:start:{mid}:{lvl}"))
    kb.add(InlineKeyboardButton(f"⬅️ К уровню", callback_data=f"level:{mid}:{lvl}"))
    bot.send_message(msg.chat.id,
        "🤖 <b>Обратная связь от ИИ</b>\n\n"
        f"💬 {result.get('praise','')}\n\n"
        f"✍️ <b>Исправленный вариант:</b>\n{result.get('corrected_text','—')}\n\n"
        f"📌 <b>Разбор:</b>\n{exp_txt}\n\n"
        f"{score_line}\n\n"
        f"🎯 <b>Совет:</b> {result.get('next_micro_task','')}\n\n"
        f"🏷 <b>Ошибки:</b> {format_tags(tags)} | ⭐ <b>+{xp} XP</b>",
        reply_markup=kb)

# ── Home: control test ────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("ctrl:start:"))
def on_ctrl_start(call):
    bot.answer_callback_query(call.id)
    parts = call.data.split(":")
    mid, lvl = parts[2], int(parts[3])
    uid = call.from_user.id
    ctrl = MODULES[mid]["levels"][lvl]["control_test"]
    storage.set_progress(uid, "mode", "ctrl_test")
    storage.set_progress(uid, "ctrl_mod", mid)
    storage.set_progress(uid, "ctrl_lvl", str(lvl))
    storage.set_progress(uid, "ctrl_idx", "0")
    storage.set_progress(uid, "ctrl_score", "0")
    att = storage.get_control_test_attempts(uid, mid, lvl)
    m = MODULES[mid]
    bot.edit_message_text(
        f"📝 <b>Контрольная{' (попытка #'+str(att+1)+')' if att else ''}</b>\n"
        f"{m['emoji']} {m['title']} — Уровень {lvl}\n\n"
        f"Нужно правильно ответить на <b>{CTRL_THRESHOLD} из {len(ctrl)}</b>. Удачи! 💪",
        call.message.chat.id, call.message.message_id)
    _send_ctrl_q(call.message.chat.id, call.message.message_id, uid)

def _send_ctrl_q(chat_id, msg_id, uid):
    mid = storage.get_progress(uid, "ctrl_mod", "")
    lvl = int(storage.get_progress(uid, "ctrl_lvl", "1"))
    idx = int(storage.get_progress(uid, "ctrl_idx", "0"))
    ctrl = MODULES[mid]["levels"][lvl]["control_test"]
    if idx >= len(ctrl):
        _finish_ctrl(uid, chat_id, msg_id); return
    q = ctrl[idx]
    kb = InlineKeyboardMarkup(row_width=1)
    for i, o in enumerate(q["options"]):
        kb.add(InlineKeyboardButton(o, callback_data=f"ctrlans:{i}"))
    bot.edit_message_text(f"📝 <b>Контрольная</b> ({idx+1}/{len(ctrl)})\n\n{q['q']}",
                          chat_id, msg_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("ctrlans:"))
def on_ctrl_ans(call):
    uid = call.from_user.id
    if storage.get_progress(uid, "mode") != "ctrl_test":
        bot.answer_callback_query(call.id, "Контрольная не активна."); return
    mid = storage.get_progress(uid, "ctrl_mod", "")
    lvl = int(storage.get_progress(uid, "ctrl_lvl", "1"))
    idx = int(storage.get_progress(uid, "ctrl_idx", "0"))
    ctrl = MODULES[mid]["levels"][lvl]["control_test"]
    if idx >= len(ctrl):
        bot.answer_callback_query(call.id); return
    chosen = int(call.data.split(":")[1])
    q = ctrl[idx]
    ok = chosen == q["correct"]
    sc = int(storage.get_progress(uid, "ctrl_score", "0"))
    if ok: sc += 1
    storage.set_progress(uid, "ctrl_score", str(sc))
    storage.set_progress(uid, "ctrl_idx", str(idx+1))
    bot.answer_callback_query(call.id, "✅ Верно!" if ok else f"❌ Правильно: {q['options'][q['correct']]}")
    _send_ctrl_q(call.message.chat.id, call.message.message_id, uid)

def _finish_ctrl(uid, chat_id, msg_id):
    mid = storage.get_progress(uid, "ctrl_mod", "")
    lvl = int(storage.get_progress(uid, "ctrl_lvl", "1"))
    sc = int(storage.get_progress(uid, "ctrl_score", "0"))
    ctrl_total = len(MODULES[mid]["levels"][lvl]["control_test"])
    passed = sc >= CTRL_THRESHOLD
    storage.set_progress(uid, "mode", "idle")
    storage.record_control_test(uid, mid, lvl, sc, passed)
    m = MODULES[mid]
    kb = InlineKeyboardMarkup()
    if passed:
        storage.mark_level_ctrl_passed(uid, mid, lvl)
        storage.add_xp(uid, XP_CTRL)
        nxt = lvl + 1
        if nxt <= 3:
            storage.set_module_level_unlocked(uid, mid, nxt)
            kb.add(InlineKeyboardButton(f"🔓 Перейти к Уровню {nxt}", callback_data=f"level:{mid}:{nxt}"))
        else:
            maybe_unlock(uid, f"module_{mid}")
        att = storage.get_control_test_attempts(uid, mid, lvl)
        if att == 1: maybe_unlock(uid, "ctrl_first_pass")
        update_meta(uid)
        res = (f"🎉 <b>Контрольная сдана!</b>\n\nРезультат: <b>{sc}/{ctrl_total}</b>\n"
               f"⭐ <b>+{XP_CTRL} XP</b>\n\n"
               + (f"🔓 Уровень {nxt} открыт!" if nxt <= 3 else f"🏆 Модуль '{m['title']}' полностью пройден!"))
    else:
        res = (f"😔 <b>Не сдана.</b>\n\nРезультат: <b>{sc}/{ctrl_total}</b>\n"
               f"Нужно минимум <b>{CTRL_THRESHOLD}</b>.\n\nПовтори задания и попробуй снова!")
        kb.add(InlineKeyboardButton("🔁 Попробовать снова", callback_data=f"ctrl:start:{mid}:{lvl}"))
        kb.add(InlineKeyboardButton("📖 Повторить задания", callback_data=f"tasks:start:{mid}:{lvl}"))
    kb.add(InlineKeyboardButton(f"⬅️ {m['emoji']} Уровни", callback_data=f"module:{mid}"))
    kb.add(InlineKeyboardButton("📚 К модулям", callback_data="menu:home"))
    bot.edit_message_text(res, chat_id, msg_id, reply_markup=kb)

# ── Fallback & run ────────────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: True)
def fallback(msg):
    bot.send_message(msg.chat.id, "Напиши /start или используй кнопки меню.", reply_markup=kb_main())

if __name__ == "__main__":
    storage.init_db()
    print("Bot v3 (Dual-mode) running...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=25)