# xp_rules.py — Единый источник правды по начислению XP.
#
# Здесь лежат ВСЕ числа, по которым бот начисляет и списывает XP, и функция
# render_xp_rules_text(), которая собирает из них текст справки для ученика.
# Правило простое: ни один XP-литерал не должен появляться в handlers —
# только импорт отсюда. Тогда экран «Как начисляется XP» не может разойтись
# с реальным поведением бота.
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# ── Коды источников начисления (пишутся в xp_ledger.source) ──────────────────
SRC_STREAK = "streak"                  # ежедневный вход
SRC_DIAG_TEST = "diag_test"            # входной тест уровня
SRC_HINT = "hint"                      # списание за подсказку
SRC_MODULE_TASK = "module_task"        # задание тренажёра
SRC_MODULE_WRITING = "module_writing"  # письменное задание тренажёра (ИИ)
SRC_MODULE_CTRL = "module_ctrl"        # контрольная тренажёра
SRC_CLASS_CORRECT = "class_correct"    # верный ответ в уроке класса
SRC_CLASS_LESSON = "class_lesson"      # завершение урока класса
SRC_CLASS_HOMEWORK = "class_homework"  # домашнее задание класса (ИИ)
SRC_KTP_PRACTICE = "ktp_practice"      # практика КТП
SRC_KTP_EXAM = "ktp_exam"              # мини-контрольная КТП
SRC_KTP_EXAM_PASS = "ktp_exam_pass"    # бонус за первую сдачу мини-контрольной
SRC_KTP_WRITING = "ktp_writing"        # письмо КТП (ИИ)
SRC_BACKFILL = "backfill"              # разовый пересчёт истории
SRC_LEGACY = "legacy"                  # начисление без указанного источника

SOURCE_LABELS: Dict[str, str] = {
    SRC_STREAK: "Ежедневный вход",
    SRC_DIAG_TEST: "Входной тест",
    SRC_HINT: "Подсказки",
    SRC_MODULE_TASK: "Задания тренажёров",
    SRC_MODULE_WRITING: "Письменные задания тренажёров",
    SRC_MODULE_CTRL: "Контрольные тренажёров",
    SRC_CLASS_CORRECT: "Верные ответы в уроке класса",
    SRC_CLASS_LESSON: "Завершение урока класса",
    SRC_CLASS_HOMEWORK: "Домашнее задание класса",
    SRC_KTP_PRACTICE: "Практика (КТП)",
    SRC_KTP_EXAM: "Мини-контрольная (КТП)",
    SRC_KTP_EXAM_PASS: "Сдача мини-контрольной",
    SRC_KTP_WRITING: "Письмо с ИИ (КТП)",
    SRC_BACKFILL: "Пересчёт истории",
    SRC_LEGACY: "Прочее",
}


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source or "", SOURCE_LABELS[SRC_LEGACY])


# ── Общие ────────────────────────────────────────────────────────────────────
HINT_COST = 2                 # списывается за одну подсказку (в любом режиме)
XP_STREAK_DAILY = 5           # за продолжение серии ежедневных занятий
XP_DIAG_CORRECT = 2           # за верный ответ во входном тесте (только 1 раз)

# ── Тренажёры (self-study модули) ────────────────────────────────────────────
XP_MODULE_TASK_BY_LEVEL: Dict[int, int] = {1: 5, 2: 10, 3: 15}
XP_MODULE_WRITING_BASE = 10
XP_MODULE_WRITING_PER_SCORE = 2
XP_MODULE_CTRL = 30
MODULE_CTRL_THRESHOLD = 5     # верных из 8, чтобы сдать контрольную тренажёра

# ── Уроки класса (режим «Мой класс» по расписанию) ───────────────────────────
XP_CLASS_CORRECT = 3
XP_CLASS_LESSON_DEFAULT = 20  # бонус за урок, если в контенте не задан свой
XP_HOMEWORK_BASE = 15
XP_HOMEWORK_PER_SCORE = 2

# ── КТП (основной учебный план) ──────────────────────────────────────────────
XP_KTP_PRACTICE_PER_POINT = 2   # за каждый балл сверх лучшего результата
XP_KTP_EXAM_PER_POINT = 3       # то же, но в мини-контрольной
XP_KTP_EXAM_FIRST_PASS = 25     # разовый бонус за первую сдачу
XP_KTP_WRITING_FIRST_BASE = 10
XP_KTP_WRITING_PER_OVERALL = 3
XP_KTP_WRITING_PER_IMPROVEMENT = 5

KTP_PASS_RATIO_NUM = 3          # порог сдачи ≈ 3/5 = 60 % вопросов
KTP_PASS_RATIO_DEN = 5
KTP_PASS_MIN = 4                # запасной порог, если размер контрольной неизвестен

# ── Ранги и достижения ───────────────────────────────────────────────────────
RANKS: List[Tuple[Optional[int], str]] = [
    (100, "🌱 Новичок"),
    (300, "🔍 Исследователь"),
    (600, "🧠 Знаток"),
    (None, "🌍 Посол культуры"),
]
ACH_XP_THRESHOLDS: Tuple[int, ...] = (100, 300)


# ── Чистые функции расчёта ───────────────────────────────────────────────────
def hint_cost() -> int:
    return HINT_COST


def streak_xp() -> int:
    return XP_STREAK_DAILY


def diag_correct_xp() -> int:
    return XP_DIAG_CORRECT


def rank_from_xp(xp: int) -> str:
    xp = int(xp or 0)
    for threshold, title in RANKS:
        if threshold is None or xp < threshold:
            return title
    return RANKS[-1][1]


def exam_pass_threshold(total: int) -> int:
    """Сколько верных нужно, чтобы зачесть мини-контрольную КТП (≈60 %)."""
    total = int(total or 0)
    if total <= 0:
        return KTP_PASS_MIN
    need = (total * KTP_PASS_RATIO_NUM + KTP_PASS_RATIO_DEN - 1) // KTP_PASS_RATIO_DEN
    return max(1, min(total, need))


def module_task_xp(level: int, task_xp: Optional[int] = None) -> int:
    if task_xp is not None:
        return int(task_xp)
    return XP_MODULE_TASK_BY_LEVEL.get(int(level or 1), XP_MODULE_TASK_BY_LEVEL[1])


def module_writing_xp(score: int) -> int:
    return XP_MODULE_WRITING_BASE + int(score or 0) * XP_MODULE_WRITING_PER_SCORE


def module_ctrl_xp() -> int:
    return XP_MODULE_CTRL


def class_correct_xp() -> int:
    return XP_CLASS_CORRECT


def class_lesson_xp(lesson_xp: Optional[int] = None) -> int:
    if lesson_xp is None:
        return XP_CLASS_LESSON_DEFAULT
    return int(lesson_xp)


def homework_xp(ai_score: int) -> int:
    return XP_HOMEWORK_BASE + int(ai_score or 0) * XP_HOMEWORK_PER_SCORE


def ktp_mcq_xp(kind: str, prev_best: int, correct: int) -> int:
    """XP за практику/мини-контрольную КТП: только за улучшение лучшего результата."""
    improvement = max(0, int(correct or 0) - int(prev_best or 0))
    per_point = XP_KTP_PRACTICE_PER_POINT if kind == "p" else XP_KTP_EXAM_PER_POINT
    return improvement * per_point


def ktp_exam_first_pass_xp() -> int:
    return XP_KTP_EXAM_FIRST_PASS


def ktp_writing_xp(first_attempt: bool, overall: int, coherence: int, prev_best: int) -> int:
    overall = int(overall or 0)
    if first_attempt:
        return XP_KTP_WRITING_FIRST_BASE + overall * XP_KTP_WRITING_PER_OVERALL + int(coherence or 0)
    improvement = max(0, overall - int(prev_best or 0))
    return improvement * XP_KTP_WRITING_PER_IMPROVEMENT


# ── Текст справки (собирается из констант выше) ──────────────────────────────
def render_xp_rules_text() -> str:
    """Русский HTML-текст «Как начисляется XP». Все числа берутся из констант."""
    task_levels = " · ".join(
        f"ур.{lvl} — <b>+{xp} XP</b>" for lvl, xp in sorted(XP_MODULE_TASK_BY_LEVEL.items())
    )
    ranks = " → ".join(
        f"{title} ({'до ' + str(threshold) if threshold else str(RANKS[-2][0]) + '+'} XP)"
        for threshold, title in RANKS
    )
    return (
        "ℹ️ <b>Как начисляется XP</b>\n\n"
        "📘 <b>Учебный план (КТП)</b>\n"
        f"🧩 Практика — <b>+{XP_KTP_PRACTICE_PER_POINT} XP</b> за каждый верный ответ "
        "сверх твоего лучшего результата по этому уроку.\n"
        f"📝 Мини‑контрольная — <b>+{XP_KTP_EXAM_PER_POINT} XP</b> за каждый верный ответ "
        "сверх лучшего результата.\n"
        f"🏅 Первая сдача мини‑контрольной — разовый бонус <b>+{XP_KTP_EXAM_FIRST_PASS} XP</b> "
        f"(сдача — это ≈{KTP_PASS_RATIO_NUM * 100 // KTP_PASS_RATIO_DEN}% верных ответов).\n"
        f"✍️ Письмо с ИИ — первая попытка <b>+{XP_KTP_WRITING_FIRST_BASE} XP</b> "
        f"и ещё <b>+{XP_KTP_WRITING_PER_OVERALL} XP</b> за каждый балл оценки ИИ (из 5) "
        "плюс балл за связность. Повторная попытка — "
        f"<b>+{XP_KTP_WRITING_PER_IMPROVEMENT} XP</b> за каждый балл улучшения.\n\n"
        "🧠 <b>Тренажёры</b>\n"
        f"Задание: {task_levels}.\n"
        f"✍️ Письменное задание — <b>+{XP_MODULE_WRITING_BASE} XP</b> и ещё "
        f"<b>+{XP_MODULE_WRITING_PER_SCORE} XP</b> за каждый балл оценки ИИ.\n"
        f"📝 Контрольная — <b>+{XP_MODULE_CTRL} XP</b> за первую сдачу "
        f"(нужно ≥{MODULE_CTRL_THRESHOLD} верных).\n\n"
        "🏫 <b>Уроки класса</b>\n"
        f"Верный ответ — <b>+{XP_CLASS_CORRECT} XP</b>. "
        f"Завершение урока — <b>+{XP_CLASS_LESSON_DEFAULT} XP</b> (в некоторых уроках больше).\n"
        f"📝 Домашнее задание — <b>+{XP_HOMEWORK_BASE} XP</b> и ещё "
        f"<b>+{XP_HOMEWORK_PER_SCORE} XP</b> за каждый балл оценки ИИ.\n\n"
        "➕ <b>Разное</b>\n"
        f"🔥 Ежедневный вход подряд — <b>+{XP_STREAK_DAILY} XP</b> в день.\n"
        f"🎯 Входной тест — <b>+{XP_DIAG_CORRECT} XP</b> за верный ответ (только первый раз).\n"
        f"💡 Подсказка — <b>−{HINT_COST} XP</b>.\n\n"
        "❗️ <b>Важно: XP даётся только за прогресс.</b>\n"
        "Если пройти тот же урок второй раз и не улучшить результат, XP не начислится — "
        "так баллы нельзя «нафармить» на одной теме.\n\n"
        f"🏆 <b>Ранги:</b> {ranks}."
    )
