"""Журнал XP: начисления по урокам, инвариант «сумма журнала == общий XP»,
разовый пересчёт истории и настройки класса (выключатель ИИ)."""
import importlib
import json
import os
import random
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import xp_rules

ENV_KEYS = (
    "RUTUTOR_DB_PATH", "BOT_DB_PATH", "RUTUTOR_DATA_DIR",
    "RUTUTOR_LEGACY_DB_PATH", "BOT_LEGACY_DB_PATH",
)


def unload_storage():
    mod = sys.modules.pop("storage", None)
    if mod is not None:
        con = getattr(mod, "_con", None)
        if con is not None:
            con.close()


class LedgerTestBase(unittest.TestCase):
    def setUp(self):
        self.old_env = {k: os.environ.get(k) for k in ENV_KEYS}
        unload_storage()
        self.tmp = tempfile.TemporaryDirectory()
        target = Path(self.tmp.name) / "bot.db"
        # Создаём пустой файл заранее: иначе _ensure_db_file_ready() найдёт
        # чужой bot.db рядом с проектом и скопирует его в тест.
        sqlite3.connect(str(target)).close()
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["RUTUTOR_DB_PATH"] = str(target)
        self.storage = importlib.import_module("storage")
        self.storage.init_db()

    def tearDown(self):
        unload_storage()
        self.tmp.cleanup()
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def set_xp(self, user_id, xp):
        con = self.storage._get_con()
        con.execute("UPDATE users SET xp=? WHERE user_id=?", (xp, user_id))
        con.commit()


class TestAwardXp(LedgerTestBase):
    def test_award_writes_ledger_row(self):
        s = self.storage
        s.upsert_user(1, "Аня", "anya")
        self.assertEqual(s.award_xp(1, 20, xp_rules.SRC_KTP_PRACTICE, "s1_01"), 20)

        rows = s.get_xp_by_lesson(1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["lesson_key"], "s1_01")
        self.assertEqual(rows[0]["scope"], "ktp")
        self.assertEqual(rows[0]["xp"], 20)
        self.assertEqual(s.get_user_xp(1), 20)

    def test_scope_detection(self):
        s = self.storage
        s.upsert_user(1, "Аня", "")
        s.award_xp(1, 5, xp_rules.SRC_STREAK)
        s.award_xp(1, 5, xp_rules.SRC_MODULE_TASK, "mod:noun:2")
        s.award_xp(1, 3, xp_rules.SRC_CLASS_CORRECT, "class:2026-03-04")
        s.award_xp(1, 2, xp_rules.SRC_KTP_PRACTICE, "s2_09")
        scopes = {r["lesson_key"]: r["scope"] for r in s.get_xp_by_lesson(1)}
        self.assertEqual(scopes[""], "global")
        self.assertEqual(scopes["mod:noun:2"], "module")
        self.assertEqual(scopes["class:2026-03-04"], "class")
        self.assertEqual(scopes["s2_09"], "ktp")

    def test_clamped_deduction_is_logged_as_applied(self):
        """users.xp не уходит ниже нуля, поэтому в журнал пишется реальное списание."""
        s = self.storage
        s.upsert_user(1, "Аня", "")
        s.award_xp(1, 1, xp_rules.SRC_KTP_PRACTICE, "s1_01")
        applied = s.award_xp(1, -5, xp_rules.SRC_HINT, "s1_01")
        self.assertEqual(applied, -1)
        self.assertEqual(s.get_user_xp(1), 0)
        self.assertEqual(s.get_xp_ledger_total(1), 0)

    def test_add_xp_wrapper_still_works(self):
        s = self.storage
        s.upsert_user(1, "Аня", "")
        s.add_xp(1, 7)
        self.assertEqual(s.get_user_xp(1), 7)
        self.assertEqual(dict(s.get_xp_by_source(1))["legacy"], 7)

    def test_unknown_user_is_ignored(self):
        self.assertEqual(self.storage.award_xp(999, 10, xp_rules.SRC_STREAK), 0)
        self.assertEqual(self.storage.get_xp_ledger_total(999), 0)

    def test_random_sequence_keeps_totals_in_sync(self):
        s = self.storage
        s.upsert_user(1, "Аня", "")
        rng = random.Random(42)
        sources = [xp_rules.SRC_KTP_PRACTICE, xp_rules.SRC_KTP_EXAM, xp_rules.SRC_HINT]
        keys = ["s1_01", "s1_02", "mod:noun:1", ""]
        for _ in range(60):
            s.award_xp(1, rng.randint(-5, 15), rng.choice(sources), rng.choice(keys))
        self.assertEqual(sum(r["xp"] for r in s.get_xp_by_lesson(1)), s.get_user_xp(1))


class TestGroupReports(LedgerTestBase):
    def _group_with_students(self):
        s = self.storage
        group = s.create_group("10-А")
        for uid, name in ((1, "Аня"), (2, "Боря")):
            s.upsert_user(uid, name, name.lower())
            s.join_group_by_code(uid, group["join_code"], role="student")
        return group

    def test_group_matrix_and_totals(self):
        s = self.storage
        group = self._group_with_students()
        gid = int(group["group_id"])
        s.award_xp(1, 90, xp_rules.SRC_KTP_PRACTICE, "s1_01")
        s.award_xp(1, 60, xp_rules.SRC_KTP_PRACTICE, "s1_02")
        s.award_xp(2, 40, xp_rules.SRC_KTP_PRACTICE, "s1_01")

        matrix = s.get_group_lesson_xp_matrix(gid)
        self.assertEqual(matrix[1]["s1_01"], 90)
        self.assertEqual(matrix[2]["s1_01"], 40)

        totals = dict((k, (xp, n)) for k, xp, n in s.get_group_lesson_xp_totals(gid))
        self.assertEqual(totals["s1_01"], (130, 2))
        self.assertEqual(totals["s1_02"], (60, 1))

    def test_export_rows(self):
        s = self.storage
        group = self._group_with_students()
        gid = int(group["group_id"])
        s.award_xp(1, 90, xp_rules.SRC_KTP_PRACTICE, "s1_01")
        s.award_xp(1, 5, xp_rules.SRC_STREAK)
        rows = s.export_group_xp_rows(gid, ["s1_01", "s1_02"])
        top = rows[0]
        self.assertEqual(top["user_id"], 1)
        self.assertEqual(top["xp_s1_01"], 90)
        self.assertEqual(top["xp_s1_02"], 0)
        self.assertEqual(top["xp_other"], 5)      # стрик не привязан к уроку
        self.assertEqual(top["xp_total"], top["xp_ledger_total"])


class TestBackfill(LedgerTestBase):
    def _seed(self):
        s = self.storage
        s.upsert_user(1, "Боря", "borya")
        s.upsert_ktp_progress(1, "s1_01", practice_score=10)
        s.upsert_ktp_progress(1, "s1_01", exam_score=7)
        s.set_ktp_cache("s1_01", {"practice": [{}] * 12, "exam": [{}] * 8})
        s.save_writing_submission(1, "s1_01", "текст", {"scores": {"overall": 4, "coherence": 3}})
        self.set_xp(1, 777)

    def test_backfill_reconstructs_and_reconciles(self):
        s = self.storage
        self._seed()
        stats = s.backfill_xp_ledger_once()
        self.assertEqual(stats["users"], 1)
        self.assertEqual(stats["lesson_rows"], 1)
        self.assertEqual(stats["legacy_rows"], 1)

        by_lesson = {r["lesson_key"]: r["xp"] for r in s.get_xp_by_lesson(1)}
        expected_lesson = (
            10 * xp_rules.XP_KTP_PRACTICE_PER_POINT
            + 7 * xp_rules.XP_KTP_EXAM_PER_POINT
            + xp_rules.XP_KTP_EXAM_FIRST_PASS
            + xp_rules.ktp_writing_xp(True, 4, 3, 0)
        )
        self.assertEqual(by_lesson["s1_01"], expected_lesson)
        self.assertEqual(by_lesson[s.LEGACY_BUCKET_KEY], 777 - expected_lesson)
        self.assertEqual(s.get_xp_ledger_total(1), 777)

    def test_backfill_runs_once(self):
        s = self.storage
        self._seed()
        s.backfill_xp_ledger_once()
        again = s.backfill_xp_ledger_once()
        self.assertEqual(again["skipped"], 1)
        self.assertEqual(s.get_xp_ledger_total(1), 777)

    def test_force_converges_to_same_totals(self):
        s = self.storage
        self._seed()
        s.backfill_xp_ledger_once()
        before = s.get_xp_by_lesson(1)
        s.backfill_xp_ledger_once(force=True)
        self.assertEqual(s.get_xp_by_lesson(1), before)
        self.assertEqual(s.get_xp_ledger_total(1), 777)

    def test_live_awards_after_backfill_keep_invariant(self):
        s = self.storage
        self._seed()
        s.backfill_xp_ledger_once()
        s.award_xp(1, 12, xp_rules.SRC_KTP_PRACTICE, "s1_02")
        self.assertEqual(s.get_user_xp(1), 789)
        self.assertEqual(s.get_xp_ledger_total(1), 789)
        self.assertEqual(sum(r["xp"] for r in s.get_xp_by_lesson(1)), 789)


class TestGroupSettings(LedgerTestBase):
    def test_ai_switch_defaults_to_on(self):
        s = self.storage
        s.upsert_user(1, "Аня", "")
        self.assertTrue(s.is_ai_enabled_for(1))
        self.assertTrue(s.are_hints_enabled_for(1))

    def test_teacher_can_disable_ai_for_the_group(self):
        s = self.storage
        group = s.create_group("10-А")
        s.upsert_user(1, "Аня", "")
        s.join_group_by_code(1, group["join_code"], role="student")
        s.set_group_setting(int(group["group_id"]), "ai_enabled", "0")
        self.assertFalse(s.is_ai_enabled_for(1))
        self.assertTrue(s.are_hints_enabled_for(1))
        s.set_group_setting(int(group["group_id"]), "ai_enabled", "1")
        self.assertTrue(s.is_ai_enabled_for(1))

    def test_teacher_is_not_limited_by_the_switch(self):
        s = self.storage
        group = s.create_group("10-А")
        s.upsert_user(1, "Аня", "")
        s.upsert_user(9, "Учитель", "")
        s.join_group_by_code(1, group["join_code"], role="student")
        s.join_group_by_code(9, group["join_code"], role="teacher")
        s.set_group_setting(int(group["group_id"]), "ai_enabled", "0")
        self.assertFalse(s.is_ai_enabled_for(1))
        self.assertTrue(s.is_ai_enabled_for(9))

    def test_disabled_in_any_group_means_disabled(self):
        s = self.storage
        g1, g2 = s.create_group("A"), s.create_group("B")
        s.upsert_user(1, "Аня", "")
        s.join_group_by_code(1, g1["join_code"], role="student")
        s.join_group_by_code(1, g2["join_code"], role="student")
        s.set_group_setting(int(g2["group_id"]), "hints_enabled", "0")
        self.assertFalse(s.are_hints_enabled_for(1))


class TestQuestionReports(LedgerTestBase):
    def test_report_is_saved_and_deduplicated(self):
        s = self.storage
        s.upsert_user(1, "Аня", "")
        question = {"id": "s1_01_q11", "q": "Текст вопроса", "options": ["а", "б", "в", "г"], "correct": 0}
        s.add_question_report(1, "s1_01", question, "а")
        self.assertEqual(s.count_question_reports("new"), 1)
        self.assertTrue(s.has_recent_question_report(1, "Текст вопроса"))
        self.assertFalse(s.has_recent_question_report(1, "Другой вопрос"))

        report = s.list_question_reports("new")[0]
        self.assertEqual(report["question_id"], "s1_01_q11")
        self.assertEqual(json.loads(report["options_json"]), ["а", "б", "в", "г"])

        s.resolve_question_report(report["id"])
        self.assertEqual(s.count_question_reports("new"), 0)
        self.assertEqual(len(s.list_question_reports("all")), 1)


if __name__ == "__main__":
    unittest.main()
