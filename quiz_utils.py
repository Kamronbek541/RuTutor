from __future__ import annotations

import json
import random
from html import escape
from typing import Any, Dict, List, Optional


def safe_html(value: Any) -> str:
    """Escape text before sending it with Telegram parse_mode='HTML'."""
    return escape(str(value or ""), quote=False)


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold().replace("ё", "е")


def _options(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    seen = set()
    for item in raw:
        text = str(item).strip()
        key = _norm(text)
        if text and key not in seen:
            out.append(text)
            seen.add(key)
    return out


def _coerce_correct_idx(raw: Any, options: List[str]) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if 0 <= raw < len(options) else None
    try:
        idx = int(str(raw).strip())
        if 0 <= idx < len(options):
            return idx
    except (TypeError, ValueError):
        pass

    raw_norm = _norm(raw)
    if raw_norm:
        for i, opt in enumerate(options):
            if _norm(opt) == raw_norm:
                return i
    return None


def _put_correct_option(options: List[str], answer: str) -> tuple[List[str], int]:
    answer = str(answer).strip()
    if not answer:
        return (options or ["Ответ не задан"]), 0

    for i, opt in enumerate(options):
        if _norm(opt) == _norm(answer):
            if i < 4:
                return options[:4], i
            return options[:3] + [answer], 3

    if len(options) >= 4:
        options = options[:3] + [answer]
        return options, 3
    options = options + [answer]
    return options, len(options) - 1


def _known_repair(question: Dict[str, Any], options: List[str]) -> Optional[Dict[str, Any]]:
    q_text = str(question.get("q", "") or "")
    q_norm = _norm(q_text)

    # A common bad AI-generated item from the screenshots: the right comparative
    # form "тише" was missing from options.
    if "тихо" in q_norm and "сравн" in q_norm and ("нареч" in q_norm or "степен" in q_norm):
        answer = "тише всего" if "превосход" in q_norm else "тише"
        fixed_options, correct = _put_correct_option(options, answer)
        return {
            **question,
            "q": q_text or "Выберите правильную степень сравнения наречия «тихо»:",
            "options": fixed_options,
            "correct": correct,
            "tag": question.get("tag") or "spelling",
        }

    # Another bad generated item can be semantically ambiguous: both "его книга"
    # and "её книга" are grammatical without context. Replace it with an
    # agreement question that has one defensible answer.
    if "местоим" in q_norm and "книга" in q_norm and ("его/его" in q_norm or "его/ее" in q_norm or "его/её" in q_norm):
        return {
            **question,
            "q": "Выберите правильное местоимение: «У меня есть ___ книга».",
            "options": ["моя", "мой", "моё", "мои"],
            "correct": 0,
            "tag": question.get("tag") or "agreement",
        }

    return None


def safe_correct_idx(question: Dict[str, Any]) -> int:
    options = _options(question.get("options", []))
    if not options:
        return 0
    idx = _coerce_correct_idx(question.get("correct", 0), options)
    return idx if idx is not None else 0


def normalize_mcq(question: Dict[str, Any], fallback_id: str = "q1") -> Dict[str, Any]:
    q = dict(question or {})
    q.setdefault("id", fallback_id)
    q["q"] = str(q.get("q", "") or "").strip() or "Выберите правильный ответ."

    options = _options(q.get("options", []))
    repaired = _known_repair(q, options)
    if repaired:
        q = repaired
        options = _options(q.get("options", []))

    idx = _coerce_correct_idx(q.get("correct", 0), options)
    if idx is None:
        raw = q.get("correct", "")
        if isinstance(raw, str) and raw.strip() and not raw.strip().isdigit():
            options, idx = _put_correct_option(options, raw)
        else:
            idx = 0

    if not options:
        options = ["Ответ не задан"]
        idx = 0

    if len(options) > 4:
        correct_answer = options[idx] if 0 <= idx < len(options) else options[0]
        if idx >= 4:
            options = options[:3] + [correct_answer]
            idx = 3
        else:
            options = options[:4]

    if idx < 0 or idx >= len(options):
        idx = 0

    q["options"] = options
    q["correct"] = idx
    q["tag"] = str(q.get("tag", "") or "vocab")
    return q


def normalize_quiz(items: Any, lesson_id: str = "", kind: str = "q", limit: Optional[int] = None) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        fallback_id = f"{lesson_id}_{kind}{i + 1}" if lesson_id else f"{kind}{i + 1}"
        out.append(normalize_mcq(item, fallback_id=fallback_id))
        if limit and len(out) >= limit:
            break
    return out


def normalize_package(package: Dict[str, Any], lesson_id: str = "") -> Dict[str, Any]:
    data = dict(package or {})
    data["practice"] = normalize_quiz(data.get("practice", []), lesson_id, "p", 12)
    data["exam"] = normalize_quiz(data.get("exam", []), lesson_id, "e", 8)
    return data


def shuffle_mcq(question: Dict[str, Any], rng: Optional[random.Random] = None) -> Dict[str, Any]:
    """Return a normalized question with shuffled options and corrected index."""
    rng = rng or random.SystemRandom()
    q = normalize_mcq(question)
    options = list(q.get("options", []))
    correct = safe_correct_idx(q)
    pairs = list(enumerate(options))
    rng.shuffle(pairs)
    q["options"] = [opt for _, opt in pairs]
    q["correct"] = next((new_i for new_i, (old_i, _) in enumerate(pairs) if old_i == correct), 0)
    return q


def build_quiz_attempt(
    items: Any,
    lesson_id: str = "",
    kind: str = "q",
    limit: Optional[int] = None,
    *,
    seed: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Build one randomized quiz attempt: question order and options both change."""
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    tasks = normalize_quiz(items, lesson_id, kind, limit)
    tasks = [shuffle_mcq(q, rng) for q in tasks]
    rng.shuffle(tasks)
    return tasks


def add_quiz_result(results_json: str, question: Dict[str, Any], chosen_idx: int) -> str:
    try:
        results = json.loads(results_json or "[]")
        if not isinstance(results, list):
            results = []
    except Exception:
        results = []

    q = normalize_mcq(question, fallback_id=f"q{len(results) + 1}")
    correct_idx = safe_correct_idx(q)
    options = q.get("options", [])
    chosen_text = options[chosen_idx] if 0 <= chosen_idx < len(options) else "—"
    correct_text = options[correct_idx] if 0 <= correct_idx < len(options) else "—"
    results.append({
        "idx": len(results) + 1,
        "q": q.get("q", ""),
        "chosen": chosen_text,
        "correct": correct_text,
        "ok": chosen_idx == correct_idx,
        "tag": q.get("tag", ""),
    })
    return json.dumps(results, ensure_ascii=False)


def build_answer_review(
    tasks: List[Dict[str, Any]],
    results_json: str,
    *,
    show_all_correct: bool = True,
    max_wrong: int = 5,
    max_all: int = 12,
) -> str:
    try:
        results = json.loads(results_json or "[]")
        if not isinstance(results, list):
            results = []
    except Exception:
        results = []

    if not results and tasks:
        for i, task in enumerate(tasks[:max_all], 1):
            q = normalize_mcq(task, fallback_id=f"q{i}")
            idx = safe_correct_idx(q)
            opts = q.get("options", [])
            results.append({
                "idx": i,
                "q": q.get("q", ""),
                "chosen": "",
                "correct": opts[idx] if 0 <= idx < len(opts) else "—",
                "ok": True,
            })

    if not results:
        return ""

    lines = ["", "", "📌 <b>Разбор ответов</b>"]
    wrong = [r for r in results if not r.get("ok")]
    if wrong:
        lines.append("<b>Ошибки:</b>")
        for r in wrong[:max_wrong]:
            q_text = safe_html(r.get("q", ""))
            if len(q_text) > 90:
                q_text = q_text[:87].rstrip() + "..."
            lines.append(
                f"❌ {int(r.get('idx') or 0)}. {q_text}\n"
                f"Твой ответ: {safe_html(r.get('chosen', '—'))}\n"
                f"Правильно: <b>{safe_html(r.get('correct', '—'))}</b>"
            )
        if len(wrong) > max_wrong:
            lines.append(f"И ещё ошибок: {len(wrong) - max_wrong}.")
    else:
        lines.append("✅ Ошибок нет.")

    if show_all_correct:
        lines.append("")
        lines.append("<b>Правильные ответы:</b>")
        for r in results[:max_all]:
            icon = "✅" if r.get("ok") else "❌"
            lines.append(f"{icon} {int(r.get('idx') or 0)}. <b>{safe_html(r.get('correct', '—'))}</b>")
        if len(results) > max_all:
            lines.append(f"...и ещё {len(results) - max_all}.")

    return "\n".join(lines)
