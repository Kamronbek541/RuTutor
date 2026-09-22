# question_reports.py — кнопка «⚠️ Ошибка в вопросе» и уведомление админам.
#
# Зачем отдельный модуль: вопросы перемешиваются на каждой попытке
# (quiz_utils.build_quiz_attempt), поэтому «11-й вопрос» у разных учеников —
# разные вопросы. Жалоба сохраняет точный текст вопроса, варианты и ключ,
# и учителю больше не нужно называть номер.
from __future__ import annotations

from typing import Any, Dict

import storage
from quiz_utils import safe_correct_idx, safe_html
from utils import xp_lesson_label

THANKS_TEXT = "Спасибо! Вопрос отправлен на проверку преподавателю."
ALREADY_TEXT = "Этот вопрос уже отправлен на проверку."


def _correct_text(question: Dict[str, Any]) -> str:
    options = question.get("options") or []
    idx = safe_correct_idx(question)
    return str(options[idx]) if 0 <= idx < len(options) else ""


def report_question(bot, user_id: int, lesson_key: str, question: Dict[str, Any], call=None) -> None:
    """Сохранить жалобу и уведомить админов. Безопасно при любых сбоях отправки."""
    q_text = str((question or {}).get("q") or "")

    if storage.has_recent_question_report(user_id, q_text):
        if call is not None:
            bot.answer_callback_query(call.id, ALREADY_TEXT, show_alert=True)
        return

    correct = _correct_text(question)
    report_id = storage.add_question_report(user_id, lesson_key, question, correct)

    if call is not None:
        bot.answer_callback_query(call.id, THANKS_TEXT, show_alert=True)

    user = storage.get_user(user_id) or {}
    options = (question or {}).get("options") or []
    text = (
        f"⚠️ <b>Жалоба на вопрос #{report_id}</b>\n\n"
        f"Ученик: {safe_html(user.get('first_name') or user_id)}\n"
        f"Урок: {safe_html(xp_lesson_label(lesson_key))}\n"
        f"ID вопроса: <code>{safe_html((question or {}).get('id') or '—')}</code>\n\n"
        f"{safe_html(q_text)}\n\n"
        f"Варианты: {safe_html(' | '.join(str(o) for o in options))}\n"
        f"Ключ: <b>{safe_html(correct or '—')}</b>\n\n"
        f"Посмотреть все: /admin → Жалобы на вопросы"
    )

    try:
        from admin import ADMIN_IDS
    except Exception:
        return
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            pass
