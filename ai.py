# ai.py — OpenAI helpers: writing evaluation + KTP lesson generation
from __future__ import annotations

import os
import json
from typing import Dict, Any, Optional, List

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "").strip()

# Lazy init so the bot can start even without OpenAI configured.
_client: Optional[OpenAI] = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def _ensure_openai() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set in .env")
    if not OPENAI_MODEL:
        raise RuntimeError("OPENAI_MODEL is not set in .env")
    if _client is None:
        raise RuntimeError("OpenAI client is not initialised")
    return _client


def _safe_json(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {
        "praise": "Хорошая попытка!",
        "corrected_text": "",
        "explanations": ["Не удалось обработать ответ модели. Попробуй ещё раз."],
        "next_micro_task": "Напиши ещё одно предложение.",
        "error_tags": ["unknown"],
        "score": 1,
        "scores": {"grammar": 1, "spelling": 1, "coherence": 1, "vocabulary": 1, "overall": 1},
        "tips": ["Старайся писать короткими предложениями и проверяй окончания."],
    }


# ── Writing evaluation (grammar + spelling + coherence/logic + vocabulary) ────
_ALLOWED_TAGS = [
    "agreement", "gender", "word_order", "spelling", "vocab", "case", "conjugation",
    "declension", "aspect", "case_ending", "gender_agreement", "short_adj", "numeral",
    "imperative", "participle", "punctuation", "syntax", "literature", "no_error"
]


def evaluate_writing_full(
    student_text: str,
    level: str,
    lesson_title: str,
    focus: str,
    lt: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns JSON:
      praise, corrected_text, explanations(list), tips(list), next_micro_task,
      error_tags(list), score(0..5),
      scores:{grammar,spelling,coherence,vocabulary,overall} (0..5)
    """
    client = _ensure_openai()
    lt_text = lt or "(нет отдельной лексической темы)"

    system_prompt = f"""
Ты — очень понятный, добрый, но точный тьютор по русскому языку для узбекоговорящих учеников академических лицеев.
Уровень ученика: {level}
Урок: {lesson_title}
Лексическая тема (Л.Т.): {lt_text}

Главный грамматический фокус: {focus}

Нужно проверить текст по 4 направлениям:
1) Грамматика/морфология (окончания, падежи, согласование, спряжение и т.д.)
2) Орфография
3) Связность и логика (есть ли понятный смысл, связки, порядок мыслей)
4) Лексика (подходит ли словарь к теме, есть ли повторения, можно ли заменить словами из Л.Т.)

Правила:
- Пиши очень просто, без лингвистических кодов.
- Ошибки называй по-русски: "Падежные окончания", "Спряжение глагола", "Согласование", "Орфография", "Пунктуация".
- Дай исправленный вариант текста.
- Дай 3–6 коротких объяснений/замечаний (bullet points).
- Дай 2–4 конкретные рекомендации (tips): что улучшить.
- Дай микро-задание на 1 минуту.

Верни СТРОГО JSON.

Теги ошибок (используй только из списка):
{_ALLOWED_TAGS}

Формат JSON (строго):
{{
  "praise": "строка",
  "corrected_text": "строка",
  "explanations": ["строка 1", "строка 2"],
  "tips": ["совет 1", "совет 2"],
  "next_micro_task": "строка",
  "error_tags": ["tag1","tag2"],
  "scores": {{
    "grammar": 0,
    "spelling": 0,
    "coherence": 0,
    "vocabulary": 0,
    "overall": 0
  }},
  "score": 0
}}

Оценки 0..5.
overall = среднее (округли) из 4 оценок.
Если ошибок нет, error_tags=["no_error"].
"""
    resp = client.responses.create(
        model=OPENAI_MODEL,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Проверь текст ученика:\n\n{student_text}"},
        ],
    )
    data = _safe_json(getattr(resp, "output_text", ""))

    # Normalize
    data.setdefault("praise", "Хорошая попытка!")
    data.setdefault("corrected_text", "")
    data.setdefault("explanations", [])
    data.setdefault("tips", [])
    data.setdefault("next_micro_task", "Составь ещё 1 предложение по теме.")
    data.setdefault("error_tags", ["unknown"])
    data.setdefault("scores", {})
    data.setdefault("score", data.get("scores", {}).get("overall", 1))

    if not isinstance(data.get("explanations"), list):
        data["explanations"] = [str(data["explanations"])]
    if not isinstance(data.get("tips"), list):
        data["tips"] = [str(data["tips"])]
    if not isinstance(data.get("error_tags"), list):
        data["error_tags"] = [str(data["error_tags"])]

    # filter tags
    tags = []
    for t in data["error_tags"]:
        if t in _ALLOWED_TAGS:
            tags.append(t)
    data["error_tags"] = tags or ["unknown"]

    # scores
    scores = data.get("scores") or {}
    def _clamp(x):
        try:
            return max(0, min(int(x), 5))
        except Exception:
            return 1

    scores_out = {
        "grammar": _clamp(scores.get("grammar", 1)),
        "spelling": _clamp(scores.get("spelling", 1)),
        "coherence": _clamp(scores.get("coherence", 1)),
        "vocabulary": _clamp(scores.get("vocabulary", 1)),
        "overall": _clamp(scores.get("overall", scores.get("overall", 1))),
    }
    # Recompute overall if missing
    if "overall" not in scores:
        scores_out["overall"] = int(round(
            (scores_out["grammar"] + scores_out["spelling"] + scores_out["coherence"] + scores_out["vocabulary"]) / 4
        ))
    data["scores"] = scores_out
    data["score"] = scores_out["overall"]

    return data


def evaluate_student_text(student_text: str, level: str, topic: str) -> Dict[str, Any]:
    """Backward-compatible wrapper (used in class mode)."""
    return evaluate_writing_full(student_text, level, topic, focus="морфология и орфография", lt=None)


def evaluate_morphology_writing(student_text: str, level: str, module_title: str, module_level: int) -> Dict[str, Any]:
    """Backward-compatible wrapper for module writing tasks."""
    focus_map = {
        "Имя существительное": "склонение существительных, падежные окончания, предлоги + падеж",
        "Имя прилагательное": "согласование по роду/числу/падежу, краткие формы, степени сравнения",
        "Числительное": "управление и склонение числительных, написание числительных",
        "Глагол": "спряжение, времена, вид, повелительное наклонение",
    }
    focus = focus_map.get(module_title, "морфология и орфография")
    return evaluate_writing_full(student_text, level, f"{module_title} (ур. {module_level})", focus=focus, lt=None)


# ── KTP lesson content generation ─────────────────────────────────────────────
def predefined_ktp_package(lesson_id: str) -> Optional[Dict[str, Any]]:
    """
    Hand-crafted packages for the two critical lessons (Причастия) so MVP works instantly.
    Package keys:
      theory(str), vocab([[ru, uz],...]), practice(list[mcq]), exam(list[mcq]), writing_prompt(str), recommended_repeat(list)
    """
    if lesson_id == "s2_07":
        practice = [
            {"id":"s2_07_q1","q":"Какое слово — причастие?","options":["читать","читающий","читатель","чтение"],"correct":1,"tag":"participle"},
            {"id":"s2_07_q2","q":"Действительное причастие прошедшего времени:","options":["пишущий","писавший","писанный","писать"],"correct":1,"tag":"participle"},
            {"id":"s2_07_q3","q":"Страдательное причастие прошедшего времени:","options":["делающий","делавший","сделанный","делать"],"correct":2,"tag":"participle"},
            {"id":"s2_07_q4","q":"Как правильно: ___ письмо (написать)","options":["написающее","написанное","написавшее","написываемое"],"correct":1,"tag":"agreement"},
            {"id":"s2_07_q5","q":"Краткая форма: 'дверь ___' (закрыть)","options":["закрытая","закрыта","закрытый","закрывая"],"correct":1,"tag":"short_adj"},
            {"id":"s2_07_q6","q":"Суффикс действительного причастия наст. вр. чаще всего:","options":["-вш-","-ющ-/-ащ-","-енн-","-т-"],"correct":1,"tag":"participle"},
            {"id":"s2_07_q7","q":"Суффикс страдательного причастия прош. вр. чаще всего:","options":["-ущ-","-ющ-","-енн-/-нн-","-а-"],"correct":2,"tag":"spelling"},
            {"id":"s2_07_q8","q":"Выбери правильное:","options":["строящий дом (дом строят)","строящий дом (он строит)","строимый дом (он строит)","строивший дом (дом строят)"],"correct":1,"tag":"participle"},
        ]
        exam = [
            {"id":"s2_07_e1","q":"Причастие отвечает на вопрос…","options":["что делать?","какой?","сколько?","где?"],"correct":1,"tag":"participle"},
            {"id":"s2_07_e2","q":"Действительное причастие наст. вр.:","options":["прочитанный","читающий","прочитан","прочитать"],"correct":1,"tag":"participle"},
            {"id":"s2_07_e3","q":"Страдательное причастие прош. вр.:","options":["делающий","сделавший","сделанный","делать"],"correct":2,"tag":"participle"},
            {"id":"s2_07_e4","q":"Краткая форма: 'письмо ___'","options":["написано","написанное","написанный","написавшее"],"correct":0,"tag":"short_adj"},
            {"id":"s2_07_e5","q":"Выбери правильное согласование:","options":["построенный дом","построенная дом","построено дом","построенные дом"],"correct":0,"tag":"agreement"},
            {"id":"s2_07_e6","q":"Суффикс -вш- чаще всего означает…","options":["наст. время","прош. время","будущее","повелительное"],"correct":1,"tag":"participle"},
        ]
        return {
            "theory": (
                "📖 <b>Причастие</b> — это форма глагола, которая отвечает на вопрос <b>какой?</b> "
                "и обозначает признак по действию.\n\n"
                "<b>1) Действительные причастия</b> (кто сам делает действие):\n"
                "• наст. вр.: чита<b>ющий</b>, пиш<b>ущий</b>\n"
                "• прош. вр.: чита<b>вший</b>, напис<b>авший</b>\n\n"
                "<b>2) Страдательные причастия</b> (с предметом сделали действие):\n"
                "• наст. вр.: чита<b>емый</b>, стро<b>имый</b>\n"
                "• прош. вр.: прочита<b>нн</b>ый, постро<b>енн</b>ый\n\n"
                "<b>3) Краткие страдательные</b>: письмо написан<b>о</b>, двери закрыт<b>ы</b>.\n\n"
                "🇺🇿 Подсказка: учи суффиксы как «формулы»."
            ),
            "vocab": [
                ["технология", "texnologiya"],
                ["приложение", "ilova"],
                ["устройство", "qurilma"],
                ["пользователь", "foydalanuvchi"],
                ["обновление", "yangilanish"],
                ["безопасность", "xavfsizlik"],
                ["интернет", "internet"],
                ["настройки", "sozlamalar"],
            ],
            "practice": practice,
            "exam": exam,
            "writing_prompt": (
                "✍️ <b>Л.Т.: Современные технологии</b>\n"
                "Напиши 4–6 предложений о технологии/приложении, которое ты используешь.\n"
                "Используй <b>минимум 2 причастия</b> (например: «обновлённое приложение», «помогающая программа»)."
            ),
            "recommended_repeat": [
                {"title":"Суффиксы причастий", "action":"Повтори -ущ-/-ющ-, -вш-, -енн-/-нн-."},
                {"title":"Краткие формы", "action":"Потренируй: закрыт/закрыта/закрыто/закрыты."},
            ],
        }

    if lesson_id == "s2_08":
        practice = [
            {"id":"s2_08_q1","q":"Как правильно?","options":["прочитаный","прочитанный","прочитаннй","прочитан"],"correct":1,"tag":"spelling"},
            {"id":"s2_08_q2","q":"Где пишется НН?","options":["сделаный","сделанный","сделан","сделано"],"correct":1,"tag":"spelling"},
            {"id":"s2_08_q3","q":"Краткая форма: 'работа ___'","options":["сделанная","сделанна","сделана","сделанная"],"correct":2,"tag":"short_adj"},
            {"id":"s2_08_q4","q":"'___прочитанная книга' (нет зависимых слов)","options":["не прочитанная","непрочитанная","не-прочитанная","оба"],"correct":1,"tag":"spelling"},
            {"id":"s2_08_q5","q":"'книга ___ мной'","options":["непрочитанная","не прочитанная","непрочитана","не прочитана"],"correct":1,"tag":"spelling"},
            {"id":"s2_08_q6","q":"Выбери правильное согласование:","options":["написанное работа","написанная работа","написанную работа","написанный работа"],"correct":1,"tag":"agreement"},
            {"id":"s2_08_q7","q":"Где ошибка?","options":["закрытая дверь","закрыта дверь","двери закрыты","закрытое окно"],"correct":1,"tag":"agreement"},
            {"id":"s2_08_q8","q":"Как правильно?","options":["организованый","организованный","организован","организована"],"correct":1,"tag":"spelling"},
        ]
        exam = [
            {"id":"s2_08_e1","q":"В полном страдательном причастии прош. вр. чаще пишется…","options":["Н","НН","не пишется","как угодно"],"correct":1,"tag":"spelling"},
            {"id":"s2_08_e2","q":"Краткая форма: 'письмо ___'","options":["прочитанный","прочитано","прочитанное","прочитающий"],"correct":1,"tag":"short_adj"},
            {"id":"s2_08_e3","q":"НЕ пишется слитно, если…","options":["есть зависимые слова","нет зависимых слов","всегда","никогда"],"correct":1,"tag":"spelling"},
            {"id":"s2_08_e4","q":"'не прочитанная мной книга' — как пишется НЕ?","options":["слитно","раздельно","через дефис","оба"],"correct":1,"tag":"spelling"},
            {"id":"s2_08_e5","q":"Выбери правильное:","options":["организованная встреча","организованая встреча","организованна встреча","организовано встреча"],"correct":0,"tag":"spelling"},
            {"id":"s2_08_e6","q":"Краткая форма чаще с одной Н:","options":["прочитан","прочитанный","прочитанная","прочитанное"],"correct":0,"tag":"spelling"},
        ]
        return {
            "theory": (
                "📖 <b>Правописание причастий</b> (самое важное):\n\n"
                "1) <b>Н/НН</b> в страдательных причастиях прош. вр.:\n"
                "• обычно <b>НН</b>: сдела<b>нн</b>ый, прочита<b>нн</b>ый\n"
                "• краткая форма — чаще <b>Н</b>: сдела<b>н</b>, прочита<b>н</b>о\n\n"
                "2) <b>НЕ</b> с причастиями:\n"
                "• слитно: <b>непрочитанная</b> книга (нет зависимых слов)\n"
                "• раздельно: <b>не прочитанная</b> мной книга (есть зависимые слова)\n\n"
                "3) Причастие согласуется как прилагательное: написан<b>ная</b> работа."
            ),
            "vocab": [
                ["телефон", "telefon"],
                ["сообщение", "xabar"],
                ["экран", "ekran"],
                ["уведомление", "bildirishnoma"],
                ["привычка", "odat"],
                ["внимание", "diqqat"],
                ["пауза", "tanaffus"],
                ["полезный", "foydali"],
            ],
            "practice": practice,
            "exam": exam,
            "writing_prompt": (
                "✍️ <b>Л.Т.: Мой день без телефона</b>\n"
                "Напиши 5–7 предложений: как прошёл бы твой день без телефона.\n"
                "Используй <b>2 страдательных причастия</b> и 1 пример с <b>НЕ</b>."
            ),
            "recommended_repeat": [
                {"title":"Н/НН", "action":"Сделанный/прочитанный (НН), сделан/прочитан (Н)."},
                {"title":"НЕ с причастиями", "action":"Зависимые слова → раздельно: «не прочитанная мной»."},
            ],
        }

    return None


def generate_ktp_package_via_ai(
    lesson_id: str,
    lesson_title: str,
    lt: Optional[str],
    kind: str,
) -> Dict[str, Any]:
    """
    Generate a full lesson package (theory + vocab + 8 practice MCQ + 6 exam MCQ + writing prompt) as JSON.
    Returned keys: theory, vocab, practice, exam, writing_prompt, recommended_repeat
    """
    client = _ensure_openai()
    lt_text = lt or "(нет отдельной лексической темы)"

    system = (
        "Ты — методист по русскому языку для узбекоговорящих учеников академических лицеев. "
        "Сделай урок-тренажёр для Telegram-бота. "
        "Структура урока: 1) теория (коротко и понятно), 2) словарик Л.Т., 3) практика (8 тестов), "
        "4) мини-контрольная (6 тестов), 5) письменное задание.\n\n"
        "Верни СТРОГО JSON со структурой:\n"
        "{theory:str, vocab:[[ru,uz],...], practice:[{id,q,options,correct,tag}], exam:[{id,q,options,correct,tag}], "
        "writing_prompt:str, recommended_repeat:[{title,action},...]}\n\n"
        "Теги (tag) выбирай из: [agreement, gender, spelling, vocab, case, conjugation, declension, aspect, participle, "
        "punctuation, syntax, literature, word_order].\n"
        "У каждого вопроса должно быть 4 варианта и 1 правильный."
    )

    user = (
        f"lesson_id: {lesson_id}\n"
        f"Тема урока: {lesson_title}\n"
        f"Лексическая тема (Л.Т.): {lt_text}\n"
        f"Тип урока: {kind} (grammar/literature/control)\n\n"
        "Сделай урок очень понятным. Примеры — из жизни лицеиста Узбекистана (школа, друзья, спорт, технологии, семья). "
        "Письменное задание: 4–7 предложений. В нём попроси использовать тему урока (например, падежи/спряжение/причастия) "
        "и слова из лексического минимума."
    )

    resp = client.responses.create(
        model=OPENAI_MODEL,
        input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )

    data = _safe_json(getattr(resp, "output_text", ""))

    # Basic validation / repair
    data.setdefault("theory", "")
    data.setdefault("vocab", [])
    data.setdefault("practice", [])
    data.setdefault("exam", [])
    data.setdefault("writing_prompt", "")
    data.setdefault("recommended_repeat", [])

    if not isinstance(data.get("practice"), list):
        data["practice"] = []
    if not isinstance(data.get("exam"), list):
        data["exam"] = []

    # Trim
    data["practice"] = data["practice"][:8]
    data["exam"] = data["exam"][:6]

    # Ensure IDs
    for i, q in enumerate(data["practice"]):
        q.setdefault("id", f"{lesson_id}_p{i+1}")
    for i, q in enumerate(data["exam"]):
        q.setdefault("id", f"{lesson_id}_e{i+1}")

    return data
