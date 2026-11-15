"""clean_description: a portal description is always plain text, never dict or JSON text."""

import unittest

from scrapers.base import clean_description


class TestCleanDescription(unittest.TestCase):
    def test_dict_takes_georgian_first(self):
        self.assertEqual(clean_description({"ka": "იყიდება", "en": "For sale"}), "იყიდება")

    def test_dict_falls_back_in_order(self):
        self.assertEqual(clean_description({"ka": None, "text": "", "en": "For sale"}), "For sale")
        self.assertEqual(
            clean_description({"ka": "", "allLanguageTogather": "ერთად", "ru": "Проდაётся"}),
            "ერთად",
        )

    def test_empty_dict_is_empty_text(self):
        raw = {
            "ka": None,
            "en": None,
            "ru": None,
            "allLanguageTogather": "",
            "serializedText": None,
            "text": None,
        }
        self.assertEqual(clean_description(raw), "")

    def test_none_is_empty_text(self):
        self.assertEqual(clean_description(None), "")

    def test_json_text_takes_ka(self):
        raw = '{\n  "KA": "იყიდება ბინა\\nვაკეში",\n  "RU": "Проდაётся"\n}'
        self.assertEqual(clean_description(raw), "იყიდება ბინა\nვაკეში")

    def test_double_encoded_json_with_zwnj(self):
        raw = '\u200c{\\"KA\\":\\"იყიდება 1 ოთახიანი ბინა\\"}'
        self.assertEqual(clean_description(raw), "იყიდება 1 ოთახიანი ბინა")

    def test_truncated_double_encoded_json(self):
        raw = '{\\"KA\\":\\"იყიდება 1 ოთახიანი ბინა მეტრო'
        self.assertEqual(clean_description(raw), "იყიდება 1 ოთახიანი ბინა მეტრო")

    def test_plain_text_is_unchanged(self):
        for raw in ("იყიდება ბინა {ახალი}", "{not json", '{"foo": "bar"}', "\u200cქირავდება ბინა"):
            self.assertEqual(clean_description(raw), raw)

    def test_json_without_text_keeps_nothing(self):
        self.assertEqual(clean_description('{"KA": "", "EN": null}'), "")


if __name__ == "__main__":
    unittest.main()
