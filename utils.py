# utils.py — Human-readable tag labels and formatters

import re

TAG_LABELS = {
    "verb":            "Глагол",
    "gender":          "Род существительного",
    "number":          "Форма числа",
    "case":            "Падеж",
    "case_ending":     "Падежные окончания",
    "agreement":       "Согласование прил. с сущ.",
    "gender_agreement":"Согласование по роду",
    "conjugation":     "Спряжение глагола",
    "declension":      "Склонение",
    "aspect":          "Вид глагола",
    "past":            "Прошедшее время",
    "imperative":      "Повелительное наклонение",
    "short_adj":       "Краткая форма прил.",
    "numeral":         "Числительные",
    "ordinal":         "Порядковые числительные",
    "numeral_case":    "Числительные в падеже",
    "spelling":        "Орфография",
    "word_order":      "Порядок слов",
    "vocab":           "Словарный запас",
    "morphemics":      "Состав слова",
    "word_formation":  "Словообразование",
    "no_error":        "Ошибок нет",
    "participle":      "Причастия",
    "gerund":          "Деепричастия",
    "reflexive":       "Возвратные глаголы",
    "punctuation":     "Пунктуация",
    "syntax":          "Синтаксис",
    "literature":      "Литературные темы",
    "unknown":         "Прочее",
}

TAG_ALIASES = {
    "verbs": "verb",
    "verb_aspect": "aspect",
    "verbaspect": "aspect",
    "verb tense": "past",
    "past_tense": "past",
    "reflexive_verb": "reflexive",
    "reflexive_verbs": "reflexive",
    "gerund_participle": "gerund",
    "deeprichastie": "gerund",
    "деепричастие": "gerund",
    "wordformation": "word_formation",
    "word_form": "word_formation",
    "morpheme": "morphemics",
    "agreement_aspect_verb": "agreement,aspect,verb",
    "verb,aspect": "verb,aspect",
    "agreement,aspect,verb": "agreement,aspect,verb",
}

SEVERITY_ICON = {0: "🟢", 1: "🟢", 2: "🟡", 3: "🟡", 4: "🔴", 5: "🔴"}


def normalize_tag(tag: str) -> str:
    key = str(tag or "").strip().casefold().replace(" ", "_").replace("-", "_")
    key = key.strip("_")
    return TAG_ALIASES.get(key, key)


def split_tags(tags) -> list:
    if not tags:
        return []
    raw_items = tags if isinstance(tags, list) else [tags]
    out = []
    for raw in raw_items:
        for part in re.split(r"[,;/|]+", str(raw or "")):
            tag = normalize_tag(part)
            if tag:
                out.append(tag)
    return list(dict.fromkeys(out))


def label(tag: str) -> str:
    """Return human-readable label for a tag."""
    parts = split_tags(tag)
    if len(parts) > 1:
        return " / ".join(label(p) for p in parts)
    key = parts[0] if parts else normalize_tag(tag)
    return TAG_LABELS.get(key, "Прочее")


def format_tags(tags: list) -> str:
    """Format a list of error tags into readable text for AI feedback."""
    if not tags or tags == ["no_error"]:
        return "✅ Ошибок не обнаружено"
    unique = split_tags(tags)
    filtered = [t for t in unique if t != "no_error"]
    if not filtered:
        return "✅ Ошибок не обнаружено"
    return " · ".join(f"<b>{label(t)}</b>" for t in filtered)


def format_error_stats(errors: list) -> str:
    """Format error stats list [(tag, count), ...] for profile/errors screen."""
    if not errors:
        return "Пока нет данных — выполняй письменные задания!"
    lines = []
    for tag, count in errors:
        if normalize_tag(tag) == "no_error":
            continue
        icon = "🔴" if count >= 5 else ("🟡" if count >= 2 else "🟢")
        times = "раз" if count % 10 in (0, 5, 6, 7, 8, 9, 11, 12, 13, 14) or count > 20 else ("раза" if count % 10 in (2, 3, 4) else "раз")
        lines.append(f"{icon} {label(tag)}: {count} {times}")
    return "\n".join(lines) if lines else "Пока нет данных."


def truncate_text(text: str, limit: int = 3500) -> str:
    """Telegram message limit helper (keeps HTML as-is; best effort)."""
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[: max(0, limit - 20)].rstrip() + "\n…(обрезано)…"
