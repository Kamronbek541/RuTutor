"""Правила начисления XP и защита от расхождения текста справки с кодом."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xp_rules


class TestXpRules(unittest.TestCase):
    def test_exam_pass_threshold(self):
        self.assertEqual(xp_rules.exam_pass_threshold(6), 4)
        self.assertEqual(xp_rules.exam_pass_threshold(8), 5)
        self.assertEqual(xp_rules.exam_pass_threshold(12), 8)
        self.assertEqual(xp_rules.exam_pass_threshold(0), xp_rules.KTP_PASS_MIN)
        self.assertEqual(xp_rules.exam_pass_threshold(1), 1)

    def test_rank_thresholds(self):
        self.assertEqual(xp_rules.rank_from_xp(0), "🌱 Новичок")
        self.assertEqual(xp_rules.rank_from_xp(99), "🌱 Новичок")
        self.assertEqual(xp_rules.rank_from_xp(100), "🔍 Исследователь")
        self.assertEqual(xp_rules.rank_from_xp(299), "🔍 Исследователь")
        self.assertEqual(xp_rules.rank_from_xp(300), "🧠 Знаток")
        self.assertEqual(xp_rules.rank_from_xp(599), "🧠 Знаток")
        self.assertEqual(xp_rules.rank_from_xp(600), "🌍 Посол культуры")

    def test_ktp_mcq_xp_rewards_only_improvement(self):
        # Практика: 2 XP за каждый балл сверх лучшего результата.
        self.assertEqual(xp_rules.ktp_mcq_xp("p", 0, 10), 20)
        self.assertEqual(xp_rules.ktp_mcq_xp("p", 8, 10), 4)
        self.assertEqual(xp_rules.ktp_mcq_xp("p", 10, 10), 0)
        self.assertEqual(xp_rules.ktp_mcq_xp("p", 10, 7), 0)
        # Мини-контрольная: 3 XP за балл.
        self.assertEqual(xp_rules.ktp_mcq_xp("e", 0, 8), 24)
        self.assertEqual(xp_rules.ktp_mcq_xp("e", 5, 8), 9)

    def test_ktp_writing_xp(self):
        self.assertEqual(xp_rules.ktp_writing_xp(True, 4, 3, 0), 10 + 4 * 3 + 3)
        self.assertEqual(xp_rules.ktp_writing_xp(False, 4, 3, 2), 10)
        self.assertEqual(xp_rules.ktp_writing_xp(False, 2, 3, 4), 0)

    def test_module_and_class_xp(self):
        self.assertEqual(xp_rules.module_task_xp(2), 10)
        self.assertEqual(xp_rules.module_task_xp(2, 7), 7)
        self.assertEqual(xp_rules.module_writing_xp(3), 16)
        self.assertEqual(xp_rules.class_lesson_xp(None), 20)
        self.assertEqual(xp_rules.class_lesson_xp(40), 40)
        self.assertEqual(xp_rules.homework_xp(4), 23)

    def test_help_text_matches_constants(self):
        """Если кто-то поменяет число в коде, но забудет текст — тест упадёт."""
        text = xp_rules.render_xp_rules_text()
        for value in (
            xp_rules.HINT_COST,
            xp_rules.XP_STREAK_DAILY,
            xp_rules.XP_DIAG_CORRECT,
            xp_rules.XP_MODULE_CTRL,
            xp_rules.MODULE_CTRL_THRESHOLD,
            xp_rules.XP_MODULE_WRITING_BASE,
            xp_rules.XP_CLASS_CORRECT,
            xp_rules.XP_CLASS_LESSON_DEFAULT,
            xp_rules.XP_HOMEWORK_BASE,
            xp_rules.XP_KTP_PRACTICE_PER_POINT,
            xp_rules.XP_KTP_EXAM_PER_POINT,
            xp_rules.XP_KTP_EXAM_FIRST_PASS,
            xp_rules.XP_KTP_WRITING_FIRST_BASE,
            xp_rules.XP_KTP_WRITING_PER_IMPROVEMENT,
        ):
            self.assertIn(str(value), text, f"в справке нет числа {value}")
        for _, title in xp_rules.RANKS:
            self.assertIn(title, text)
        for level, xp in xp_rules.XP_MODULE_TASK_BY_LEVEL.items():
            self.assertIn(f"ур.{level}", text)
            self.assertIn(str(xp), text)

    def test_every_source_has_russian_label(self):
        codes = [v for k, v in vars(xp_rules).items() if k.startswith("SRC_")]
        self.assertTrue(codes)
        for code in codes:
            self.assertIn(code, xp_rules.SOURCE_LABELS, f"нет подписи для источника {code}")


if __name__ == "__main__":
    unittest.main()
