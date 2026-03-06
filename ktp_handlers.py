# ktp_handlers.py — KTP curriculum flow: theory → practice → exam → writing
from __future__ import annotations

import json
from typing import Dict, Any, List, Optional

import storage
from ktp_plan import KTP_LESSONS, LESSON_BY_ID, semester_lessons, TAG_TO_RECOMMEND
from ai import predefined_ktp_package, generate_ktp_package_via_ai, evaluate_writing_full
from utils import label, truncate_text


PASS_EXAM_THRESHOLD = 4  # out of 6


# Basic hints by tag
HINTS = {
    "case":        "💡 Вспомни вопросы падежей: нет кого/чего? → Род.п. | в/на (где?) → Предл.п.",
    "declension":  "💡 1 скл. (-а/-я), 2 скл. (м.р. без окончания + ср.р. -о/-е), 3 скл. (ж.р. на -ь).",
    "agreement":   "💡 Прилагательное согласуется: красивый стол, красивая книга, красивое окно, красивые дома.",
    "conjugation": "💡 I спр.: -ю,-ешь,-ет,-ем,-ете,-ют. II спр.: -ю,-ишь,-ит,-им,-ите,-ят.",
    "aspect":      "💡 'что делать?' → несов. (процесс). 'что сделать?' → сов. (результат).",
    "participle":  "💡 Причастие = глагол + признак (как прилагательное). Суффиксы: -ущ-/-ющ-, -вш-, -енн-/-нн-.",
    "spelling":    "💡 Проверь Н/НН, НЕ (слитно/раздельно) и окончания слов.",
    "syntax":      "💡 Найди главные члены: кто? что делает? Потом смотри, есть ли союзы и запятые.",
    "punctuation": "💡 Запятая часто нужна перед союзами: потому что, когда, если, чтобы и т.д.",
    "vocab":       "💡 Используй слова из словарика Л.Т. и не повторяй одно слово много раз.",
}
DEFAULT_HINT = "💡 Прочитай теорию ещё раз и подумай, какая форма слова нужна."

HINT_COST = 2  # XP cost per hint


def _safe_correct_idx(question: Dict[str, Any]) -> int:
    """Safely extract the correct answer index from a question dict.
    AI sometimes returns a string value instead of an integer index."""
    raw = question.get("correct", 0)
    if isinstance(raw, int):
        return raw
    try:
        return int(raw)
    except (ValueError, TypeError):
        # AI returned the answer text itself — find it in options
        options = question.get("options", [])
        raw_str = str(raw).strip()
        for i, opt in enumerate(options):
            if str(opt).strip() == raw_str:
                return i
        return 0  # fallback to first option


def _get_or_generate_package(lesson_id: str) -> Dict[str, Any]:
    """
    Returns a lesson package. If OpenAI isn't configured and the lesson isn't cached,
    returns a safe placeholder package (so the bot never crashes).
    """
    # 1) handcrafted
    pack = predefined_ktp_package(lesson_id)
    if pack:
        return pack
    # 2) cache
    cached = storage.get_ktp_cache(lesson_id)
    if cached:
        return cached
    # 3) generate via AI
    meta = LESSON_BY_ID.get(lesson_id)
    if not meta:
        raise KeyError("Lesson not found")

    try:
        pack = generate_ktp_package_via_ai(lesson_id, meta.title, meta.lt, meta.kind)
        storage.set_ktp_cache(lesson_id, pack)
        return pack
    except Exception as e:
        # Placeholder
        return {
            "theory": ("⚠️ Контент для этого урока ещё не сгенерирован.\n\n" + f"Причина: {e}\n\n" + "Админу нужно настроить OPENAI_API_KEY и OPENAI_MODEL в .env."),
            "vocab": [],
            "practice": [],
            "exam": [],
            "writing_prompt": "Напиши 4–6 предложений по теме урока.",
            "recommended_repeat": [],
        }



def register(bot):
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    def kb_back_menu():
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))
        return kb

    def kb_semesters(uid: int):
        kb = InlineKeyboardMarkup(row_width=1)
        for sem in [1, 2, 3]:
            lessons = semester_lessons(sem)
            done = sum(1 for l in lessons if storage.get_ktp_progress(uid, l.lesson_id).get("done"))
            kb.add(InlineKeyboardButton(f"📘 Семестр {sem} ({done}/{len(lessons)})", callback_data=f"ktp:sem:{sem}"))
        kb.add(InlineKeyboardButton("📊 Мои ошибки", callback_data="home:errors"))
        kb.add(InlineKeyboardButton("⬅️ В меню", callback_data="nav:menu"))
        return kb

    def kb_lessons(uid: int, sem: int):
        kb = InlineKeyboardMarkup(row_width=1)
        for l in semester_lessons(sem):
            p = storage.get_ktp_progress(uid, l.lesson_id)
            done = int(p.get("done") or 0) == 1
            icon = "✅" if done else ("🟡" if (p.get("exam_best",0) or 0) > 0 else "🔓")
            kb.add(InlineKeyboardButton(f"{icon} {l.num}. {l.title}", callback_data=f"ktp:lesson:{l.lesson_id}"))
        kb.add(InlineKeyboardButton("⬅️ К семестрам", callback_data="menu:ktp"))
        return kb

    def kb_lesson_actions(lesson_id: str, uid: int):
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(InlineKeyboardButton("🃏 Словарик", callback_data=f"ktp:vocab:{lesson_id}"),
               InlineKeyboardButton("🧩 Практика", callback_data=f"ktp:practice_start:{lesson_id}"))
        kb.add(InlineKeyboardButton("📝 Мини‑контрольная", callback_data=f"ktp:exam_start:{lesson_id}"),
               InlineKeyboardButton("✍️ Письмо (ИИ)", callback_data=f"ktp:write_start:{lesson_id}"))
        kb.add(InlineKeyboardButton("🔁 Повторить", callback_data=f"ktp:lesson:{lesson_id}"),
               InlineKeyboardButton("⬅️ Назад", callback_data="menu:ktp"))
        return kb

    # ── Entry from main menu ──────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data == "menu:ktp")
    def on_ktp_menu(call):
        bot.answer_callback_query(call.id)
        uid = call.from_user.id
        storage.upsert_user(uid, call.from_user.first_name, call.from_user.username)
        bot.edit_message_text(
            "📘 <b>Учебный план (КТП)</b>\n\nВыбери семестр:",
            call.message.chat.id, call.message.message_id,
            reply_markup=kb_semesters(uid),
            parse_mode="HTML",
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ktp:sem:"))
    def on_sem(call):
        bot.answer_callback_query(call.id)
        uid = call.from_user.id
        sem = int(call.data.split(":")[2])
        bot.edit_message_text(
            f"📘 <b>Семестр {sem}</b>\n\nВыбери урок:",
            call.message.chat.id, call.message.message_id,
            reply_markup=kb_lessons(uid, sem),
            parse_mode="HTML",
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ktp:lesson:"))
    def on_lesson(call):
        bot.answer_callback_query(call.id)
        uid = call.from_user.id
        lesson_id = call.data.split(":")[2]
        meta = LESSON_BY_ID.get(lesson_id)
        if not meta:
            bot.send_message(call.message.chat.id, "Урок не найден.")
            return
        p = storage.get_ktp_progress(uid, lesson_id)
        pack = _get_or_generate_package(lesson_id)
        lt = meta.lt or "—"
        status = (
            f"✅ Пройден\n"
            f"Лучшее: практика {p.get('practice_best',0)}/8 · контроль {p.get('exam_best',0)}/6 · письмо {p.get('writing_best',0)}/5"
            if int(p.get("done") or 0) == 1 else
            f"Прогресс: практика {p.get('practice_best',0)}/8 · контроль {p.get('exam_best',0)}/6 · письмо {p.get('writing_best',0)}/5"
        )
        theory = truncate_text(pack.get("theory",""), 2600)
        bot.edit_message_text(
            f"📚 <b>Урок {meta.num} (семестр {meta.semester})</b>\n"
            f"<b>{meta.title}</b>\n"
            f"Л.Т.: <i>{lt}</i>\n\n"
            f"{status}\n\n"
            f"📖 <b>Теория</b>\n{theory}",
            call.message.chat.id, call.message.message_id,
            reply_markup=kb_lesson_actions(lesson_id, uid),
            parse_mode="HTML",
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ktp:vocab:"))
    def on_vocab(call):
        bot.answer_callback_query(call.id)
        uid = call.from_user.id
        lesson_id = call.data.split(":")[2]
        meta = LESSON_BY_ID.get(lesson_id)
        if not meta:
            return
        pack = _get_or_generate_package(lesson_id)
        vocab = pack.get("vocab") or []
        if not vocab:
            txt = "🃏 <b>Словарик</b>\n\nПока нет словарика для этого урока."
        else:
            lines = []
            for ru, uz in vocab[:14]:
                lines.append(f"• <b>{ru}</b> — <i>{uz}</i>")
            txt = "🃏 <b>Словарик (Л.Т.)</b>\n\n" + "\n".join(lines)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("⬅️ К уроку", callback_data=f"ktp:lesson:{lesson_id}"))
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="HTML")

    # ── Practice flow ─────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("ktp:practice_start:"))
    def on_practice_start(call):
        bot.answer_callback_query(call.id)
        uid = call.from_user.id
        lesson_id = call.data.split(":")[2]
        pack = _get_or_generate_package(lesson_id)
        tasks = pack.get("practice") or []
        if not tasks:
            bot.answer_callback_query(call.id, "Нет заданий.", show_alert=True)
            return
        storage.set_progress(uid, "mode", "ktp_practice")
        storage.set_progress(uid, "ktp_lesson", lesson_id)
        storage.set_progress(uid, "ktp_idx", "0")
        storage.set_progress(uid, "ktp_correct", "0")
        _send_mcq(call.message.chat.id, call.message.message_id, uid, tasks, lesson_id, kind="p")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ktp:exam_start:"))
    def on_exam_start(call):
        bot.answer_callback_query(call.id)
        uid = call.from_user.id
        lesson_id = call.data.split(":")[2]
        pack = _get_or_generate_package(lesson_id)
        tasks = pack.get("exam") or []
        if not tasks:
            bot.answer_callback_query(call.id, "Нет контрольной.", show_alert=True)
            return
        storage.set_progress(uid, "mode", "ktp_exam")
        storage.set_progress(uid, "ktp_lesson", lesson_id)
        storage.set_progress(uid, "ktp_idx", "0")
        storage.set_progress(uid, "ktp_correct", "0")
        _send_mcq(call.message.chat.id, call.message.message_id, uid, tasks, lesson_id, kind="e")

    def _send_mcq(chat_id: int, msg_id: int, uid: int, tasks: List[Dict[str, Any]], lesson_id: str, kind: str):
        idx = int(storage.get_progress(uid, "ktp_idx", "0") or 0)
        if idx >= len(tasks):
            _finish_mcq(chat_id, msg_id, uid, tasks, lesson_id, kind)
            return
        t = tasks[idx]
        kb = InlineKeyboardMarkup(row_width=1)
        for i, opt in enumerate(t.get("options", [])):
            kb.add(InlineKeyboardButton(opt, callback_data=f"ktp:{kind}ans:{lesson_id}:{idx}:{i}"))
        kb.add(InlineKeyboardButton("💡 Подсказка (−2 XP)", callback_data=f"ktp:hint:{lesson_id}:{idx}:{kind}"))
        kb.add(InlineKeyboardButton("⬅️ К уроку", callback_data=f"ktp:lesson:{lesson_id}"))
        title = "Практика" if kind == "p" else "Мини‑контрольная"
        bot.edit_message_text(
            f"🧩 <b>{title}</b>\nВопрос {idx+1}/{len(tasks)}\n\n{t.get('q','')}",
            chat_id, msg_id, reply_markup=kb, parse_mode="HTML"
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ktp:hint:"))
    def on_hint(call):
        uid = call.from_user.id
        # Check XP balance before deducting
        current_xp = storage.get_user_xp(uid)
        if current_xp < HINT_COST:
            bot.answer_callback_query(call.id, "❌ Недостаточно XP для подсказки.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        _, _, lesson_id, idx_s, kind = call.data.split(":")
        idx = int(idx_s)
        pack = _get_or_generate_package(lesson_id)
        tasks = pack.get("practice" if kind == "p" else "exam") or []
        if idx >= len(tasks):
            return
        tag = (tasks[idx].get("tag") or "")
        hint = HINTS.get(tag, DEFAULT_HINT)
        storage.add_xp(uid, -HINT_COST)
        bot.answer_callback_query(call.id, hint, show_alert=True)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("ktp:pans:") or c.data.startswith("ktp:eans:"))
    def on_answer(call):
        uid = call.from_user.id
        parts = call.data.split(":")
        kind = "p" if parts[0] == "ktp" and parts[1].startswith("pans") else "e"
        lesson_id = parts[2]; idx = int(parts[3]); chosen = int(parts[4])
        mode = storage.get_progress(uid, "mode", "")
        if (kind == "p" and mode != "ktp_practice") or (kind == "e" and mode != "ktp_exam"):
            bot.answer_callback_query(call.id, "Сессия не активна.")
            return

        pack = _get_or_generate_package(lesson_id)
        tasks = pack.get("practice" if kind == "p" else "exam") or []
        cur = int(storage.get_progress(uid, "ktp_idx", "0") or 0)
        if idx != cur:
            bot.answer_callback_query(call.id, "Продолжай по порядку.")
            return
        t = tasks[idx]
        correct_idx = _safe_correct_idx(t)
        ok = chosen == correct_idx
        if ok:
            c = int(storage.get_progress(uid, "ktp_correct", "0") or 0) + 1
            storage.set_progress(uid, "ktp_correct", str(c))
            storage.add_xp(uid, 2 if kind == "p" else 3)

        # track errors for wrong answers (tag)
        if not ok and t.get("tag"):
            storage.track_errors(uid, [t.get("tag")])

        storage.set_progress(uid, "ktp_idx", str(idx + 1))
        options = t.get("options", [])
        correct_text = options[correct_idx] if 0 <= correct_idx < len(options) else "?"
        bot.answer_callback_query(call.id, "✅ Верно!" if ok else f"❌ Правильно: {correct_text}")
        _send_mcq(call.message.chat.id, call.message.message_id, uid, tasks, lesson_id, kind)

    def _finish_mcq(chat_id: int, msg_id: int, uid: int, tasks: List[Dict[str, Any]], lesson_id: str, kind: str):
        correct = int(storage.get_progress(uid, "ktp_correct", "0") or 0)
        total = len(tasks)
        storage.set_progress(uid, "mode", "idle")

        if kind == "p":
            storage.upsert_ktp_progress(uid, lesson_id, practice_score=correct)
        else:
            storage.upsert_ktp_progress(uid, lesson_id, exam_score=correct)

        pct = int(correct / max(1, total) * 100)
        grade = "🌟 Отлично!" if pct >= 90 else ("👍 Хорошо!" if pct >= 70 else ("😊 Неплохо!" if pct >= 50 else "💪 Повтори тему!"))

        passed = (kind == "e" and correct >= PASS_EXAM_THRESHOLD)
        if passed:
            storage.add_xp(uid, 25)

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("⬅️ К уроку", callback_data=f"ktp:lesson:{lesson_id}"))
        if kind == "p":
            kb.add(InlineKeyboardButton("📝 К контрольной", callback_data=f"ktp:exam_start:{lesson_id}"))
        else:
            kb.add(InlineKeyboardButton("✍️ Письмо (ИИ)", callback_data=f"ktp:write_start:{lesson_id}"))

        title = "Практика завершена" if kind == "p" else "Контрольная завершена"
        extra = ""
        if kind == "e":
            extra = "✅ Урок засчитан!" if passed else f"Нужно ≥{PASS_EXAM_THRESHOLD}/6, чтобы зачесть урок."
        bot.edit_message_text(
            f"🎉 <b>{title}</b>\n\nПравильных: <b>{correct}/{total}</b>\n{grade}\n\n{extra}",
            chat_id, msg_id, reply_markup=kb, parse_mode="HTML"
        )

        # mark done if exam passed and writing already exists
        if kind == "e" and passed:
            p = storage.get_ktp_progress(uid, lesson_id)
            writing_attempts = int(p.get("writing_attempts") or 0)
            if writing_attempts >= 1:
                storage.upsert_ktp_progress(uid, lesson_id, done=True)

    # ── Writing flow ──────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith("ktp:write_start:"))
    def on_write_start(call):
        bot.answer_callback_query(call.id)
        uid = call.from_user.id
        lesson_id = call.data.split(":")[2]
        meta = LESSON_BY_ID.get(lesson_id)
        if not meta:
            return
        pack = _get_or_generate_package(lesson_id)
        prompt = pack.get("writing_prompt") or "Напиши 4–6 предложений по теме урока."
        storage.set_progress(uid, "mode", "awaiting_ktp_text")
        storage.set_progress(uid, "ktp_write_lesson", lesson_id)
        bot.send_message(call.message.chat.id, f"{prompt}\n\n<i>Отправь ответ одним сообщением.</i>", parse_mode="HTML")

    @bot.message_handler(func=lambda m: storage.get_progress(m.from_user.id, "mode", "") == "awaiting_ktp_text")
    def on_write_text(msg):
        uid = msg.from_user.id
        text = (msg.text or "").strip()
        lesson_id = storage.get_progress(uid, "ktp_write_lesson", "")
        meta = LESSON_BY_ID.get(lesson_id)
        if not meta or not text:
            storage.set_progress(uid, "mode", "idle")
            bot.reply_to(msg, "Напиши текст одним сообщением.")
            return

        pack = _get_or_generate_package(lesson_id)

        # Define focus for evaluation
        if meta.kind == "literature":
            focus = "связность, логика, точность пересказа/мысли, орфография"
        elif meta.kind == "control":
            focus = "повторение: грамматика, орфография, связность"
        else:
            focus = meta.title

        user = storage.get_user(uid) or {}
        level = user.get("language_level", "A1")
        bot.send_message(msg.chat.id, "🤖 Проверяю текст...")

        try:
            result = evaluate_writing_full(
                student_text=text,
                level=level,
                lesson_title=meta.title,
                focus=focus,
                lt=meta.lt,
            )
        except Exception as e:
            storage.set_progress(uid, "mode", "idle")
            bot.send_message(msg.chat.id, f"⚠️ Ошибка ИИ: {e}")
            return

        storage.set_progress(uid, "mode", "idle")
        storage.save_writing_submission(uid, lesson_id, text, result)

        tags = result.get("error_tags", []) or []
        storage.track_errors(uid, tags)

        overall = int(result.get("score", 1) or 1)
        storage.upsert_ktp_progress(uid, lesson_id, writing_score=overall)

        # XP: reward by overall + sub-scores
        scores = result.get("scores") or {}
        xp = 10 + int(scores.get("overall", overall)) * 3 + int(scores.get("coherence", 1))
        storage.add_xp(uid, xp)

        # mark done if exam passed
        p = storage.get_ktp_progress(uid, lesson_id)
        if int(p.get("exam_best") or 0) >= PASS_EXAM_THRESHOLD:
            storage.upsert_ktp_progress(uid, lesson_id, done=True)

        # Build response
        sc = result.get("scores", {})
        score_line = f"📊 Оценки: грамматика {sc.get('grammar','?')}/5 · орфография {sc.get('spelling','?')}/5 · связность {sc.get('coherence','?')}/5 · лексика {sc.get('vocabulary','?')}/5\n<b>Итог:</b> {sc.get('overall', overall)}/5"

        exp = result.get("explanations", [])[:6]
        tips = result.get("tips", [])[:4]
        expl_text = "\n".join([f"• {x}" for x in exp]) if exp else "• Всё хорошо, продолжай!"
        tips_text = "\n".join([f"✅ {x}" for x in tips]) if tips else ""

        corrected = result.get("corrected_text", "").strip()
        corrected_block = f"\n\n<b>Исправленный вариант:</b>\n{truncate_text(corrected, 1200)}" if corrected else ""

        # Recommendations: what to repeat (based on tags)
        rec_lessons = []
        for t in tags:
            for lid in TAG_TO_RECOMMEND.get(t, []):
                if lid not in rec_lessons:
                    rec_lessons.append(lid)
        rec_lessons = rec_lessons[:3]
        rec_text = ""
        if rec_lessons:
            names = []
            for lid in rec_lessons:
                lm = LESSON_BY_ID.get(lid)
                if lm:
                    names.append(f"• {lm.title}")
            if names:
                rec_text = "\n\n<b>Что повторить:</b>\n" + "\n".join(names)

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("⬅️ К уроку", callback_data=f"ktp:lesson:{lesson_id}"))
        if rec_lessons:
            kb.add(InlineKeyboardButton("🔁 Повторить тему", callback_data=f"ktp:lesson:{rec_lessons[0]}"))

        bot.send_message(
            msg.chat.id,
            f"✅ <b>Проверка готова</b>\n\n"
            f"{score_line}\n\n"
            f"<b>Комментарии:</b>\n{expl_text}"
            + (f"\n\n<b>Советы:</b>\n{tips_text}" if tips_text else "")
            + corrected_block
            + rec_text
            + f"\n\n🧠 Мини‑задание: {result.get('next_micro_task','')}\n"
            f"🎁 +{xp} XP",
            parse_mode="HTML",
            reply_markup=kb,
        )
