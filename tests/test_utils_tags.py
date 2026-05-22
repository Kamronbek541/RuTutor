import unittest

from ktp_plan import TAG_TO_RECOMMEND
from utils import format_error_stats, format_tags, label, split_tags


class UtilsTagsTest(unittest.TestCase):
    def test_split_and_label_mixed_ai_tags(self):
        self.assertEqual(split_tags(["Verb,aspect", "Agreement,aspect,verb"]), ["verb", "aspect", "agreement"])
        self.assertEqual(label("Verb,aspect"), "Глагол / Вид глагола")

    def test_formatters_are_russian_for_raw_ai_tags(self):
        self.assertEqual(format_tags(["Verb,aspect"]), "<b>Глагол</b> · <b>Вид глагола</b>")
        stats = format_error_stats([("Agreement,aspect,verb", 3), ("Reflexive", 1)])
        self.assertIn("Согласование прил. с сущ. / Вид глагола / Глагол", stats)
        self.assertIn("Возвратные глаголы", stats)

    def test_new_error_tags_have_lesson_recommendations(self):
        self.assertIn("s1_05", TAG_TO_RECOMMEND["word_formation"])
        self.assertIn("s1_04", TAG_TO_RECOMMEND["morphemics"])
        self.assertIn("s2_09", TAG_TO_RECOMMEND["gerund"])
        self.assertIn("s2_03", TAG_TO_RECOMMEND["reflexive"])


if __name__ == "__main__":
    unittest.main()
