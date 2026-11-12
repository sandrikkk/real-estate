import random
import unittest

from PIL import Image, ImageDraw, ImageOps

from core.dedup import dhash, hamming
from scripts.photo_benchmark import (
    EDITS,
    METHODS,
    THRESHOLDS,
    HashIndex,
    evaluate,
    evaluate_search,
    format_report,
    listing_pair_matches,
    mirror_hash,
    mirrored_reposts,
    render_mask,
    search,
)


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


def _listing(n: int, group=None):
    return (f"L{n}", group, [_picture(n * 10 + k) for k in range(3)])


def _index(listings, districts=None):
    hashes = {lid: [dhash(img) for img in photos] for lid, _, photos in listings}
    facts = {
        lid: {
            "id": lid,
            "district": (districts or {}).get(lid, "A"),
            "group_id": group,
            "price_usd": 50000,
            "area_m2": 50,
        }
        for lid, group, _ in listings
    }
    return HashIndex(hashes, facts)


class TestEdits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.listings = [_listing(n) for n in range(12)]
        cls.report = evaluate(cls.listings)

    def test_report_has_every_method_threshold_and_edit(self):
        self.assertEqual(set(self.report), {f"{m} ≤ {t}" for m in METHODS for t in THRESHOLDS})
        for row in self.report.values():
            self.assertEqual(set(row), {*EDITS, "false matches"})

    def test_recompressed_copy_is_found(self):
        self.assertEqual(self.report["dHash ≤ 10"]["telegram"], 1.0)

    def test_mirrored_copy_is_found_only_with_the_mirrored_query(self):
        self.assertLess(self.report["dHash ≤ 10"]["mirror"], 0.5)
        self.assertEqual(self.report["dHash + mirrored query ≤ 10"]["mirror"], 1.0)

    def test_higher_threshold_never_finds_fewer(self):
        for m in METHODS:
            for col in [*EDITS, "false matches"]:
                values = [self.report[f"{m} ≤ {t}"][col] for t in THRESHOLDS]
                self.assertEqual(values, sorted(values))

    def test_same_photo_group_is_not_a_false_match(self):
        same = _picture(1)
        report = evaluate([("a", "g1", [same]), ("b", "g1", [same.copy()])])
        self.assertEqual(report["dHash ≤ 10"]["false matches"], 0.0)
        report = evaluate([("a", None, [same]), ("b", None, [same.copy()])])
        self.assertEqual(report["dHash ≤ 10"]["false matches"], 1.0)

    def test_format_report_is_a_table_row_per_method_and_threshold(self):
        text = format_report(self.report, len(self.listings))
        self.assertIn("12 photos", text)
        for name in self.report:
            self.assertIn(f"| {name} |", text)


class TestMirrorHash(unittest.TestCase):
    def test_mirroring_twice_gives_the_hash_back(self):
        h = dhash(_picture(3))
        self.assertEqual(mirror_hash(mirror_hash(h)), h)

    def test_close_to_hashing_the_mirrored_photo(self):
        for seed in range(10):
            img = _picture(seed)
            self.assertLessEqual(hamming(mirror_hash(dhash(img)), dhash(ImageOps.mirror(img))), 6)


class TestSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.listings = [_listing(n) for n in range(6)]
        cls.index = _index(cls.listings)

    def _post(self, photos, mirrored):
        out = []
        for img in photos:
            h = dhash(img)
            out.append([h, mirror_hash(h)] if mirrored else [h])
        return out

    def test_album_finds_its_listing_and_no_other(self):
        _, _, photos = self.listings[2]
        post = self._post([EDITS["telegram"](p) for p in photos], mirrored=False)
        self.assertEqual(search(self.index, post, None), ["L2"])

    def test_mirrored_album_is_found_only_with_the_mirrored_query(self):
        _, _, photos = self.listings[2]
        flipped = [EDITS["mirror"](p) for p in photos]
        self.assertEqual(search(self.index, self._post(flipped, mirrored=False), None), [])
        self.assertEqual(search(self.index, self._post(flipped, mirrored=True), None), ["L2"])

    def test_photo_in_two_districts_is_dropped(self):
        shared = _picture(999)
        listings = [
            ("a", None, [shared, _picture(1)]),
            ("b", None, [shared, _picture(2)]),
        ]
        index = _index(listings, districts={"a": "A", "b": "B"})
        post = self._post([shared, _picture(1)], mirrored=False)
        # Only one photo is left, and without a caption one photo cannot confirm.
        self.assertEqual(search(index, post, None), [])

    def test_one_photo_with_caption_is_confirmed(self):
        _, _, photos = self.listings[1]
        post = self._post(photos[:1], mirrored=False)
        self.assertEqual(search(self.index, post, None), [])
        caption = {"price_usd": 51000, "area_m2": 50}
        self.assertEqual(search(self.index, post, caption), ["L1"])

    def test_search_table_has_a_row_per_method_and_caption(self):
        text = evaluate_search(self.index, self.listings[:3])
        self.assertIn("3 albums against 6 listings", text)
        self.assertEqual(text.count("| dHash |"), 2)
        self.assertEqual(text.count("| dHash + mirrored query |"), 2)
        self.assertIn("| dHash | none | 100% / 0.00 |", text)


class TestMirroredReposts(unittest.TestCase):
    def test_counts_a_mirrored_repost(self):
        photos = [_picture(k) for k in range(3)]
        listings = [
            ("orig", None, photos),
            ("copy", None, [ImageOps.mirror(p) for p in photos]),
            ("other", None, [_picture(50 + k) for k in range(3)]),
        ]
        index = _index(listings)
        keep = ~render_mask(index)
        plain = listing_pair_matches(index, mirrored=False, keep=keep)
        flipped = listing_pair_matches(index, mirrored=True, keep=keep)
        self.assertEqual(plain, {})
        self.assertEqual(flipped[("orig", "copy")], 3)
        self.assertIn("| only when one side is mirrored | 1 |", mirrored_reposts(index))

    def test_plain_repost_is_not_counted_as_mirrored(self):
        photos = [_picture(k) for k in range(3)]
        index = _index([("a", "g", photos), ("b", "g", [p.copy() for p in photos])])
        text = mirrored_reposts(index)
        self.assertIn("| as they are | 1 |", text)
        self.assertIn("| as they are, not in one photo group | 0 |", text)
        self.assertIn("| only when one side is mirrored | 0 |", text)


if __name__ == "__main__":
    unittest.main()
