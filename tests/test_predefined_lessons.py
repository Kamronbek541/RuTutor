import unittest

from ai import predefined_ktp_package
from quiz_utils import normalize_package, safe_correct_idx


class PredefinedLessonsTest(unittest.TestCase):
    def test_first_semester_critical_lessons_are_complete(self):
        for lesson_id in ("s1_03", "s1_04", "s1_05"):
            package = normalize_package(predefined_ktp_package(lesson_id), lesson_id)

            self.assertEqual(len(package["practice"]), 12)
            self.assertEqual(len(package["exam"]), 8)
            for section in ("practice", "exam"):
                for question in package[section]:
                    self.assertEqual(len(question["options"]), 4)
                    idx = safe_correct_idx(question)
                    self.assertGreaterEqual(idx, 0)
                    self.assertLess(idx, len(question["options"]))

    def test_deeprichastie_lesson_is_complete_and_valid(self):
        package = normalize_package(predefined_ktp_package("s2_09"), "s2_09")

        self.assertIn("Деепричастие", package["theory"])
        self.assertEqual(len(package["practice"]), 12)
        self.assertEqual(len(package["exam"]), 8)

        for section in ("practice", "exam"):
            for question in package[section]:
                self.assertEqual(len(question["options"]), 4)
                idx = safe_correct_idx(question)
                self.assertGreaterEqual(idx, 0)
                self.assertLess(idx, len(question["options"]))


if __name__ == "__main__":
    unittest.main()
