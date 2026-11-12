import random
import unittest

from PIL import Image, ImageDraw

from scripts.photo_benchmark import EDITS, METHODS, evaluate, format_report


def _picture(seed: int) -> Image.Image:
    """A left-right asymmetric picture of random blocks, different for every seed."""
    rng = random.Random(seed)
    img = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(img)
    for _ in range(40):
        x, y = rng.randrange(600), rng.randrange(440)
        shade = rng.randrange(256)
        draw.rectangle(
            (x, y, x + rng.randrange(20, 200), y + rng.randrange(20, 200)), fill=(shade,) * 3
        )
    return img


class TestPhotoBenchmark(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.photos = [(None, _picture(seed)) for seed in range(12)]
        cls.report = evaluate(cls.photos)

    def test_report_has_every_method_and_edit(self):
        self.assertEqual(set(self.report), set(METHODS))
        for row in self.report.values():
            self.assertEqual(set(row), {*EDITS, "false matches"})

    def test_recompressed_copy_is_found(self):
        self.assertEqual(self.report["dHash"]["telegram"], 1.0)

    def test_mirrored_copy_is_found_only_with_the_mirrored_query(self):
        self.assertLess(self.report["dHash"]["mirror"], 0.5)
        self.assertEqual(self.report["dHash + mirrored query"]["mirror"], 1.0)

    def test_mirrored_query_never_finds_fewer_copies(self):
        for edit in EDITS:
            self.assertGreaterEqual(
                self.report["dHash + mirrored query"][edit], self.report["dHash"][edit]
            )

    def test_same_photo_group_is_not_a_false_match(self):
        same = _picture(1)
        report = evaluate([("g1", same), ("g1", same.copy())])
        self.assertEqual(report["dHash"]["false matches"], 0.0)
        report = evaluate([(None, same), (None, same.copy())])
        self.assertEqual(report["dHash"]["false matches"], 1.0)

    def test_format_report_is_a_table_row_per_method(self):
        text = format_report(self.report, len(self.photos))
        self.assertIn("12 photos", text)
        for name in METHODS:
            self.assertIn(f"| {name} |", text)


if __name__ == "__main__":
    unittest.main()
