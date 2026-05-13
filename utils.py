# utils.py — Human-readable tag labels and formatters

TAG_LABELS = {
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
    "no_error":        "Ошибок нет",
    "participle":      "Причастия",
    "punctuation":     "Пунктуация",
    "syntax":          "Синтаксис",
    "literature":      "Литературные темы",
    "unknown":         "Прочее",
}

SEVERITY_ICON = {0: "🟢", 1: "🟢", 2: "🟡", 3: "🟡", 4: "🔴", 5: "🔴"}


def label(tag: str) -> str:
    """Return human-readable label for a tag."""
    return TAG_LABELS.get(tag, tag.replace("_", " ").capitalize())


def format_tags(tags: list) -> str:
    """Format a list of error tags into readable text for AI feedback."""
    if not tags or tags == ["no_error"]:
        return "✅ Ошибок не обнаружено"
    unique = list(dict.fromkeys(tags))  # deduplicate, preserve order
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
        if tag == "no_error":
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
