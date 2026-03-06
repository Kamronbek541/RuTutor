# class_handlers.py — All class-mode (school schedule) handlers
from datetime import date as dt_date
import storage
from class_content import CLASS_SCHEDULE
from ai import evaluate_student_text
from utils import format_tags, format_error_stats, label

HINT_XP_COST = 2

# Hints per task tag
HINTS = {
    "gender":      "💡 Подсказка: смотри на окончание слова. -а/-я → ж.р., без окончания → м.р., -о/-е → ср.р.",
    "number":      "💡 Подсказка: для большинства слов мн.ч. = основа + -ы/-и. Но есть исключения: брат→братья.",
    "case":        "💡 Подсказка: вопросы! Нет кого/чего? → Родительный. Вижу кого/что? → Винительный.",
    "agreement":   "💡 Подсказка: прилагательное согласуется с существительным. М.р. → -ый/-ой, Ж.р. → -ая/-яя, Ср.р. → -ое/-ее.",
    "numeral":     "💡 Подсказка: 2,3,4 → Родит. ед.ч. (стола). 5–20 → Родит. мн.ч. (столов). 1/21 → Имен. ед.ч.",
    "ordinal":     "💡 Подсказка: порядковые = как прилагательные. 1→первый/ая/ое, 2→второй, 3→третий.",
    "conjugation": "💡 Подсказка: I спр.: -ю,-ешь,-ет,-ем,-ете,-ют. II спр.: -ю,-ишь,-ит,-им,-ите,-ят.",
    "aspect":      "💡 Подсказка: 'что делать?' → несов. (процесс). 'что сделать?' → сов. (результат).",
    "past":        "💡 Подсказка: прошедшее = основа + -л (м.р.), -ла (ж.р.), -ло (ср.р.), -ли (мн.ч.).",
    "imperative":  "💡 Подсказка: к другу → -и/-й. К учителю (вежливо) → -ите/-йте.",
    "declension":  "💡 Подсказка: 1 скл. = -а/-я (мама). 2 скл. = м.р./ср.р. на -о. 3 скл. = ж.р. на -ь (ночь).",
    "short_adj":   "💡 Подсказка: краткая форма: м.р.→нет окончания/ен, ж.р.→-а/-на, ср.р.→-о/-но, мн.ч.→-ы/-ны.",
}
DEFAULT_HINT = "💡 Подсказка: читай внимательно вопрос и вспоминай теорию из начала урока."


def get_today() -> str:
    return dt_date.today().strftime("%Y-%m-%d")


def get_available_lessons() -> list:
    today = get_today()
    return [l for l in CLASS_SCHEDULE if l["date"] <= today]


def get_future_lessons() -> list:
    today = get_today()
    return [l for l in CLASS_SCHEDULE if l["date"] > today]


def build_schedule_text(user_id: int) -> str:
    today = get_today()
    lines = []
    for lesson in CLASS_SCHEDULE:
        done = storage.is_class_lesson_done(user_id, lesson["date"])
        hw = storage.is_homework_submitted(user_id, lesson["date"])
        is_today = lesson["date"] == today
        available = lesson["date"] <= today

        if done:
            hw_icon = "📝✅" if hw else "📝⏳"
            lines.append(f"✅ {lesson['date']} — {lesson['title']} {hw_icon}")
        elif is_today:
            lines.append(f"🔓 <b>Сегодня: {lesson['title']}</b> ← открыт!")
        elif available:
            lines.append(f"⚠️ {lesson['date']} — {lesson['title']} (пропущен)")
        else:
            lines.append(f"🔒 {lesson['date']} — {lesson['title']}")
    return "\n".join(lines)


def register(bot):
    """Register all class-mode handlers on the bot instance."""

    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    def kb_back_class():
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📅 Расписание", callback_data="class:schedule"))
        kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))
        return kb

    # ── Show schedule ──────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "class:schedule")
    def on_class_schedule(call):
        bot.answer_callback_query(call.id)
        uid = call.from_user.id
        today = get_today()
        available = get_available_lessons()

        kb = InlineKeyboardMarkup()
        for lesson in available:
            done = storage.is_class_lesson_done(uid, lesson["date"])
            icon = "✅" if done else ("🔓" if lesson["date"] == today else "⚠️")
            kb.add(InlineKeyboardButton(
                f"{icon} {lesson['date']} — {lesson['title']}",
                callback_data=f"class:lesson:{lesson['id']}"
            ))
        future = get_future_lessons()
        if future:
            nxt = future[0]
            kb.add(InlineKeyboardButton(
                f"🔒 Следующий: {nxt['date']} — {nxt['title']}",
                callback_data="class:noop"
            ))
        kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))

        bot.edit_message_text(
            f"📅 <b>Расписание уроков</b>\n\n{build_schedule_text(uid)}",
            call.message.chat.id, call.message.message_id, reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda c: c.data == "class:noop")
    def on_noop(call):
        bot.answer_callback_query(call.id, "🔒 Этот урок ещё не открылся.", show_alert=True)

    # ── Open a lesson ──────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("class:lesson:"))
    def on_class_lesson(call):
        bot.answer_callback_query(call.id)
        uid = call.from_user.id
        lesson_id = call.data.split(":", 2)[2]
        lesson = next((l for l in CLASS_SCHEDULE if l["id"] == lesson_id), None)
        if not lesson:
            bot.send_message(call.message.chat.id, "Урок не найден.")
            return

        today = get_today()
        if lesson["date"] > today:
            bot.answer_callback_query(call.id, f"🔒 Урок откроется {lesson['date']}.", show_alert=True)
            return

        done = storage.is_class_lesson_done(uid, lesson["date"])
        hw_done = storage.is_homework_submitted(uid, lesson["date"])
        score = storage.get_class_lesson_score(uid, lesson["date"])

        kb = InlineKeyboardMarkup()
        if done:
            status = f"✅ <b>Урок пройден!</b> Результат: {score}/{len(lesson['tasks'])}\n"
            if not hw_done:
                kb.add(InlineKeyboardButton("📝 Сдать домашнее задание", callback_data=f"class:hw:{lesson_id}"))
            else:
                status += "📝 ДЗ сдано ✅\n"
            kb.add(InlineKeyboardButton("🔁 Повторить", callback_data=f"class:start:{lesson_id}"))
        else:
            status = ""
            kb.add(InlineKeyboardButton("▶️ Начать урок", callback_data=f"class:start:{lesson_id}"))
        kb.add(InlineKeyboardButton("📅 Расписание", callback_data="class:schedule"))

        bot.edit_message_text(
            f"{lesson['emoji']} <b>{lesson['title']}</b>\n"
            f"📅 Дата: {lesson['date']}\n\n"
            f"{status}\n{lesson['theory']}",
            call.message.chat.id, call.message.message_id, reply_markup=kb
        )

    # ── Start lesson tasks ─────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("class:start:"))
    def on_class_start(call):
        bot.answer_callback_query(call.id)
        uid = call.from_user.id
        lesson_id = call.data.split(":", 2)[2]
        lesson = next((l for l in CLASS_SCHEDULE if l["id"] == lesson_id), None)
        if not lesson:
            return
        storage.set_progress(uid, "mode", "class_lesson")
        storage.set_progress(uid, "cls_lesson_id", lesson_id)
        storage.set_progress(uid, "cls_task_idx", "0")
        storage.set_progress(uid, "cls_correct", "0")
        _send_class_task(bot, call.message.chat.id, call.message.message_id, uid, lesson)

    def _send_class_task(bot, chat_id, message_id, uid, lesson):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        idx = int(storage.get_progress(uid, "cls_task_idx", "0"))
        tasks = lesson["tasks"]
        if idx >= len(tasks):
            _finish_class_lesson(bot, chat_id, message_id, uid, lesson)
            return
        task = tasks[idx]
        hint_tag = task.get("tag", "")
        kb = InlineKeyboardMarkup(row_width=1)
        for i, opt in enumerate(task["options"]):
            kb.add(InlineKeyboardButton(opt, callback_data=f"clsans:{idx}:{i}"))
        kb.add(InlineKeyboardButton(f"💡 Подсказка (−{HINT_XP_COST} XP)", callback_data=f"class:hint:{lesson['id']}:{idx}"))
        kb.add(InlineKeyboardButton("📅 Расписание", callback_data="class:schedule"))
        bot.edit_message_text(
            f"📖 <b>Урок: {lesson['title']}</b>\n"
            f"🧩 Вопрос {idx+1}/{len(tasks)}\n\n{task['q']}",
            chat_id, message_id, reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("class:hint:"))
    def on_class_hint(call):
        uid = call.from_user.id
        # Check XP balance before deducting
        current_xp = storage.get_user_xp(uid)
        if current_xp < HINT_XP_COST:
            bot.answer_callback_query(call.id, "❌ Недостаточно XP для подсказки.", show_alert=True)
            return
        parts = call.data.split(":")
        lesson_id, idx = parts[2], int(parts[3])
        lesson = next((l for l in CLASS_SCHEDULE if l["id"] == lesson_id), None)
        if not lesson:
            bot.answer_callback_query(call.id)
            return
        task = lesson["tasks"][idx]
        tag = task.get("tag", "")
        hint_text = HINTS.get(tag, DEFAULT_HINT)
        storage.add_xp(uid, -HINT_XP_COST)
        storage.increment_hint_used(uid)
        bot.answer_callback_query(call.id, hint_text, show_alert=True)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("clsans:"))
    def on_class_answer(call):
        uid = call.from_user.id
        if storage.get_progress(uid, "mode") != "class_lesson":
            bot.answer_callback_query(call.id, "Урок не активен.")
            return
        parts = call.data.split(":")
        task_idx, chosen = int(parts[1]), int(parts[2])
        cur_idx = int(storage.get_progress(uid, "cls_task_idx", "0"))
        if cur_idx != task_idx:
            bot.answer_callback_query(call.id, "Продолжай по порядку.")
            return
        lesson_id = storage.get_progress(uid, "cls_lesson_id", "")
        lesson = next((l for l in CLASS_SCHEDULE if l["id"] == lesson_id), None)
        if not lesson:
            bot.answer_callback_query(call.id)
            return
        task = lesson["tasks"][task_idx]
        is_correct = chosen == task["correct"]
        if is_correct:
            correct = int(storage.get_progress(uid, "cls_correct", "0")) + 1
            storage.set_progress(uid, "cls_correct", str(correct))
            storage.add_xp(uid, 3)
        storage.set_progress(uid, "cls_task_idx", str(task_idx + 1))
        bot.answer_callback_query(
            call.id,
            "✅ Верно!" if is_correct else f"❌ Правильно: {task['options'][task['correct']]}"
        )
        _send_class_task(bot, call.message.chat.id, call.message.message_id, uid, lesson)

    def _finish_class_lesson(bot, chat_id, message_id, uid, lesson):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        correct = int(storage.get_progress(uid, "cls_correct", "0"))
        total = len(lesson["tasks"])
        xp = lesson.get("xp", 20)
        passed = correct >= total // 2

        storage.mark_class_lesson_done(uid, lesson["date"])
        storage.set_class_lesson_score(uid, lesson["date"], correct)
        storage.set_progress(uid, "mode", "idle")
        if passed:
            storage.add_xp(uid, xp)

        pct = int(correct / total * 100)
        if pct >= 90:
            grade = "🌟 Отлично!"
        elif pct >= 70:
            grade = "👍 Хорошо!"
        elif pct >= 50:
            grade = "😊 Неплохо!"
        else:
            grade = "💪 Повтори тему!"

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📝 Сдать домашнее задание", callback_data=f"class:hw:{lesson['id']}"))
        kb.add(InlineKeyboardButton("📅 Расписание", callback_data="class:schedule"))
        kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))

        bot.edit_message_text(
            f"🎉 <b>Урок завершён!</b>\n\n"
            f"Тема: {lesson['emoji']} {lesson['title']}\n"
            f"Правильных: <b>{correct}/{total}</b> ({pct}%) {grade}\n"
            + (f"⭐ <b>+{xp} XP</b>\n" if passed else "")
            + f"\n📝 <b>Домашнее задание:</b>\n{lesson['homework_prompt']}\n\n"
            f"<i>Напиши ответ боту — ИИ проверит и даст обратную связь!</i>",
            chat_id, message_id, reply_markup=kb
        )

    # ── Homework flow ──────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("class:hw:"))
    def on_class_hw_start(call):
        bot.answer_callback_query(call.id)
        uid = call.from_user.id
        lesson_id = call.data.split(":", 2)[2]
        lesson = next((l for l in CLASS_SCHEDULE if l["id"] == lesson_id), None)
        if not lesson:
            return
        if storage.is_homework_submitted(uid, lesson["date"]):
            bot.answer_callback_query(call.id, "ДЗ уже сдано!", show_alert=True)
            return
        storage.set_progress(uid, "mode", "awaiting_hw")
        storage.set_progress(uid, "hw_lesson_id", lesson_id)
        bot.send_message(
            call.message.chat.id,
            f"📝 <b>Домашнее задание</b>\n{lesson['emoji']} {lesson['title']}\n\n"
            f"{lesson['homework_prompt']}\n\n"
            f"<i>Напиши ответ — ИИ проверит твой текст.</i>"
        )

    @bot.message_handler(func=lambda m: storage.get_progress(m.from_user.id, "mode", "") == "awaiting_hw")
    def on_homework_text(message):
        uid = message.from_user.id
        text = (message.text or "").strip()
        if not text:
            bot.reply_to(message, "Напиши текст домашнего задания.")
            return
        lesson_id = storage.get_progress(uid, "hw_lesson_id", "")
        lesson = next((l for l in CLASS_SCHEDULE if l["id"] == lesson_id), None)
        if not lesson:
            storage.set_progress(uid, "mode", "idle")
            bot.reply_to(message, "Не удалось найти урок. Попробуй снова.")
            return

        user = storage.get_user(uid) or {}
        lang_level = user.get("language_level", "A1")
        bot.send_message(message.chat.id, "🤖 Проверяю домашнее задание...")

        try:
            result = evaluate_student_text(text, lang_level, lesson["title"])
        except Exception as e:
            storage.set_progress(uid, "mode", "idle")
            bot.send_message(message.chat.id, f"⚠️ Ошибка ИИ: {e}")
            return

        storage.set_progress(uid, "mode", "idle")
        storage.mark_homework_done(uid, lesson["date"])

        error_tags = result.get("error_tags", [])
        storage.track_errors(uid, error_tags)

        ai_score = result.get("score", 1)
        xp_hw = 15 + ai_score * 2
        storage.add_xp(uid, xp_hw)

        exps = result.get("explanations", [])
        if not isinstance(exps, list):
            exps = [str(exps)]
        exp_text = "\n".join(f"• {x}" for x in exps[:3]) or "• Отличная работа!"

        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📅 Расписание", callback_data="class:schedule"))
        kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))

        bot.send_message(
            message.chat.id,
            f"🤖 <b>Проверка ДЗ</b>\n\n"
            f"💬 {result.get('praise', 'Хорошая работа!')}\n\n"
            f"✍️ <b>Исправленный вариант:</b>\n{result.get('corrected_text', '—')}\n\n"
            f"📌 <b>Разбор ошибок:</b>\n{exp_text}\n\n"
            f"🎯 <b>Совет:</b> {result.get('next_micro_task', '')}\n\n"
            f"🏷 <b>Ошибки:</b> {format_tags(error_tags)}\n"
            f"⭐ <b>+{xp_hw} XP</b> за ДЗ!",
            reply_markup=kb
        )
