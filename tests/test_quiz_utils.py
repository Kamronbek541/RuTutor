import json
import unittest

from quiz_utils import (
    add_quiz_result,
    build_answer_review,
    normalize_mcq,
    normalize_package,
    safe_correct_idx,
)


class QuizUtilsTest(unittest.TestCase):
    def test_repairs_missing_comparative_answer(self):
        q = normalize_mcq({
            "q": "Выберите правильную сравнительную степень наречия «тихо»:",
            "options": ["тихее", "тихий", "тихо", "тишайше"],
            "correct": 0,
            "tag": "spelling",
        })

        self.assertIn("тише", q["options"])
        self.assertEqual(q["options"][q["correct"]], "тише")

    def test_replaces_ambiguous_pronoun_question(self):
        q = normalize_mcq({
            "q": "Выберите правильное местоимение: «(Его/Его) книга лежит на столе».",
            "options": ["Его", "Его", "Её", "Еёему"],
            "correct": 0,
            "tag": "agreement",
        })

        self.assertEqual(q["q"], "Выберите правильное местоимение: «У меня есть ___ книга».")
        self.assertEqual(q["options"][q["correct"]], "моя")

    def test_accepts_correct_answer_as_text(self):
        q = normalize_mcq({
            "q": "Как правильно?",
            "options": ["два", "двое", "двух", "двум"],
            "correct": "двое",
            "tag": "numeral",
        })

        self.assertEqual(safe_correct_idx(q), 1)

    def test_adds_missing_text_answer_to_options(self):
        q = normalize_mcq({
            "q": "Как правильно?",
            "options": ["тихее", "тихо", "тихий", "тишайше"],
            "correct": "тише",
            "tag": "spelling",
        })

        self.assertEqual(q["options"][q["correct"]], "тише")
        self.assertEqual(len(q["options"]), 4)

    def test_review_lists_wrong_and_all_correct_answers(self):
        tasks = normalize_package({
            "practice": [
                {
                    "q": "2 + 2 < 5?",
                    "options": ["да", "нет", "иногда", "—"],
                    "correct": 0,
                    "tag": "vocab",
                }
            ],
            "exam": [],
        })["practice"]
        results_json = add_quiz_result("[]", tasks[0], chosen_idx=1)
        results = json.loads(results_json)

        self.assertFalse(results[0]["ok"])
        review = build_answer_review(tasks, results_json)
        self.assertIn("Правильно", review)
        self.assertIn("Правильные ответы", review)
        self.assertIn("&lt;", review)


if __name__ == "__main__":
    unittest.main()
