"""Проверка целостности ВСЕХ вопросов бота.

Ловит класс ошибок, из-за которых ученик видит 3 варианта вместо 4 или
неправильный ключ: повторяющиеся варианты (в том числе отличающиеся только
регистром или буквой ё), индекс правильного ответа вне диапазона и смещение
ключа при нормализации вопроса.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import predefined_ktp_package
from class_content import CLASS_SCHEDULE
from content import MODULES, MODULE_ORDER, TEST_QUESTIONS
from ktp_plan import KTP_LESSONS
from quiz_utils import _norm, normalize_mcq


def _iter_all_questions():
    """(источник, вопрос) по всем встроенным наборам вопросов."""
    for i, q in enumerate(TEST_QUESTIONS):
        yield f"TEST_QUESTIONS[{i}]", q

    for mid in MODULE_ORDER:
        for lvl, level in MODULES[mid]["levels"].items():
            for q in level.get("tasks", []):
                yield f"MODULES[{mid}].levels[{lvl}].tasks", q
            for q in level.get("control_test", []):
                yield f"MODULES[{mid}].levels[{lvl}].control_test", q

    for lesson in CLASS_SCHEDULE:
        for q in lesson.get("tasks", []):
            yield f"CLASS_SCHEDULE[{lesson['id']}]", q

    for lesson in KTP_LESSONS:
        pack = predefined_ktp_package(lesson.lesson_id)
        if not pack:
            continue
        for group in ("practice", "exam"):
            for q in pack.get(group, []):
                yield f"predefined[{lesson.lesson_id}].{group}", q


class TestContentIntegrity(unittest.TestCase):
    def test_questions_are_well_formed(self):
        seen_ids = {}
        checked = 0
        for source, q in _iter_all_questions():
            qid = q.get("id", "?")
            where = f"{source} / {qid}"
            checked += 1

            self.assertTrue(str(q.get("q", "")).strip(), f"пустой текст вопроса: {where}")

            options = q.get("options") or []
            self.assertEqual(len(options), 4, f"должно быть 4 варианта: {where} → {options}")

            normed = [_norm(o) for o in options]
            self.assertEqual(
                len(set(normed)), 4,
                f"варианты повторяются (с учётом регистра и ё): {where} → {options}",
            )

            correct = q.get("correct")
            self.assertIsInstance(correct, int, f"correct должен быть индексом: {where}")
            self.assertTrue(0 <= correct < 4, f"correct вне диапазона: {where} → {correct}")

            # Нормализация не должна менять правильный ответ.
            expected = options[correct]
            n = normalize_mcq(dict(q))
            actual = n["options"][n["correct"]]
            self.assertEqual(expected, actual, f"ключ съехал при нормализации: {where}")

            if qid and qid != "?":
                self.assertNotIn(qid, seen_ids, f"дубликат id: {qid} ({source} и {seen_ids.get(qid)})")
                seen_ids[qid] = source

        self.assertGreater(checked, 200, "тест должен проверять весь встроенный контент")

    def test_predefined_ktp_packages_are_complete(self):
        for lesson in KTP_LESSONS:
            pack = predefined_ktp_package(lesson.lesson_id)
            if not pack:
                continue
            with self.subTest(lesson=lesson.lesson_id):
                self.assertEqual(len(pack.get("practice", [])), 12)
                self.assertEqual(len(pack.get("exam", [])), 8)
                self.assertTrue(str(pack.get("theory", "")).strip())
                self.assertTrue(str(pack.get("writing_prompt", "")).strip())
                self.assertTrue(pack.get("vocab"))

    def test_first_semester_lessons_have_verified_content(self):
        """Уроки 1 и 2 класс проходит сейчас — их контент должен быть рукописным,
        а не сгенерированным ИИ (именно там учитель нашла ошибку)."""
        for lesson_id in ("s1_01", "s1_02"):
            self.assertIsNotNone(
                predefined_ktp_package(lesson_id),
                f"для {lesson_id} нет проверенного пакета",
            )


if __name__ == "__main__":
    unittest.main()
