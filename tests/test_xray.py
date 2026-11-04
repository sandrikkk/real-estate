import os
import tempfile
import unittest
from datetime import datetime, timezone

from core.database import DatabaseEngine
from core.models import PropertyListing
from core.xray import (
    evaluate_broker_verdict,
    find_exact_duplicates,
    parse_xray_input,
    run_xray_on_db,
)


def _make_listing(
    source_id: str,
    source: str = "myhome",
    price_usd: float = 85000.0,
    area_m2: float = 60.0,
    district: str = "საბურთალო",
    floor: str = "7",
    rooms: int = 3,
    is_owner: bool = True,
    user_type: str = "physical",
    phone_number: str = "+995 599 11 22 33",
    author_id: str = "u1",
    author_listings_count: int = 1,
    group_id: str = None,
) -> PropertyListing:
    return PropertyListing(
        id=f"{source}_{source_id}",
        source=source,
        source_id=source_id,
        url=f"https://www.myhome.ge/ka/pr/{source_id}/iyideba-bina/"
        if source == "myhome"
        else f"https://home.ss.ge/ka/udzravi-qoneba/iyideba-{source_id}",
        title=f"Apartment {source_id}",
        price_usd=price_usd,
        price_gel=price_usd * 2.7,
        price_per_m2=round(price_usd / area_m2, 2) if area_m2 else 1000.0,
        area_m2=area_m2,
        floor=floor,
        rooms=rooms,
        district=district,
        city="თბილისი",
        description=f"Test listing {source_id}",
        image_urls=["https://example.com/1.jpg"],
        is_owner=is_owner,
        user_type=user_type,
        phone_number=phone_number,
        author_id=author_id,
        author_listings_count=author_listings_count,
        group_id=group_id,
        scraped_at=datetime.now(timezone.utc),
    )


class TestXRayEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_xray.db")
        self.db = DatabaseEngine(db_path=self.db_path, use_d1=False)
        self.db.init_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_xray_input_variants(self):
        # 1. MyHome URL
        res_mh = parse_xray_input("ნახე ეს ბინა https://www.myhome.ge/ka/pr/22051234/iyideba-3-otaxiani-bina/")
        self.assertIsNotNone(res_mh)
        self.assertEqual(res_mh["kind"], "url")
        self.assertEqual(res_mh["source"], "myhome")
        self.assertEqual(res_mh["source_id"], "22051234")
        self.assertEqual(res_mh["listing_id"], "myhome_22051234")

        # 2. SS.ge URL
        res_ss = parse_xray_input("https://home.ss.ge/ka/udzravi-qoneba/iyideba-3-otaxiani-bina-saburtaloze-31948512")
        self.assertIsNotNone(res_ss)
        self.assertEqual(res_ss["kind"], "url")
        self.assertEqual(res_ss["source"], "ss_ge")
        self.assertEqual(res_ss["source_id"], "31948512")
        self.assertEqual(res_ss["listing_id"], "ss_ge_31948512")

        # 3. Georgian phone variants
        res_ph1 = parse_xray_input("599 12 34 56")
        self.assertIsNotNone(res_ph1)
        self.assertEqual(res_ph1["kind"], "phone")
        self.assertEqual(res_ph1["phone_9"], "599123456")
        self.assertEqual(res_ph1["formatted_phone"], "+995 599 12 34 56")

        res_ph2 = parse_xray_input("+995-599-12-34-56")
        self.assertIsNotNone(res_ph2)
        self.assertEqual(res_ph2["kind"], "phone")
        self.assertEqual(res_ph2["phone_9"], "599123456")

        # 4. Facebook post text with apartment attributes and phone
        fb_text = (
            "იყიდება საბურთალოზე 60 კვ.მ ბინა, მე-7 სართული, "
            "3 ოთახი, ფასი 85000$, ტელ 599112233"
        )
        res_fb = parse_xray_input(fb_text)
        self.assertIsNotNone(res_fb)
        self.assertEqual(res_fb["kind"], "fb_text")
        self.assertEqual(res_fb["district"], "საბურთალო")
        self.assertAlmostEqual(res_fb["area_m2"], 60.0)
        self.assertEqual(res_fb["floor"], "7")
        self.assertEqual(res_fb["rooms"], 3)
        self.assertAlmostEqual(res_fb["price_usd"], 85000.0)
        self.assertEqual(res_fb["phone_9"], "599112233")

        # 5. Non-X-Ray inputs must return None (commands, budget range, plain chat)
        self.assertIsNone(parse_xray_input("/filter"))
        self.assertIsNone(parse_xray_input("/start"))
        self.assertIsNone(parse_xray_input("45000 70000"))
        self.assertIsNone(parse_xray_input("40 90"))
        self.assertIsNone(parse_xray_input("გამარჯობა"))

    def test_hidden_broker_verdict_from_phone_history(self):
        # Seller claims to be physical owner on each individual post,
        # but has 3 distinct apartments under the same phone number
        self.db.save_listings_batch(
            [
                _make_listing("101", area_m2=55.0, district="საბურთალო", phone_number="+995 599 11 22 33"),
                _make_listing("102", area_m2=82.0, district="ვაკე", phone_number="+995 599 11 22 33"),
                _make_listing("103", area_m2=110.0, district="დიღომი", phone_number="+995 599 11 22 33"),
            ]
        )

        report = run_xray_on_db(self.db, "599 11 22 33")
        self.assertIsNotNone(report)
        self.assertIn("🕵️‍♂️ სავარაუდო ფარული მაკლერი / სააგენტო", report)
        self.assertIn("3 განცხადება", report)

    def test_exact_duplicate_vs_different_floor_rejected(self):
        # Target listing: 60 m² in საბურთალო, floor 7, rooms 3, $85,000 (posted by broker)
        target_broker = _make_listing(
            "2001",
            source="myhome",
            price_usd=85000.0,
            area_m2=60.0,
            district="საბურთალო",
            floor="7",
            rooms=3,
            is_owner=False,
            user_type="agent",
            phone_number="+995 555 00 00 01",
            author_id="broker1",
            author_listings_count=15,
        )
        # Same apartment on SS.ge by real owner at $79,000 (floor 7, rooms 3)
        same_apt_owner = _make_listing(
            "3001",
            source="ss_ge",
            price_usd=79000.0,
            area_m2=60.5,
            district="საბურთალო",
            floor="7",
            rooms=3,
            is_owner=True,
            user_type="physical",
            phone_number="+995 599 77 88 99",
            author_id="owner1",
            author_listings_count=1,
        )
        # Different apartment in same building/district: 60 m² but floor 3 at $75,000
        different_floor_apt = _make_listing(
            "3002",
            source="ss_ge",
            price_usd=75000.0,
            area_m2=60.0,
            district="საბურთალო",
            floor="3",
            rooms=3,
            is_owner=True,
            user_type="physical",
            phone_number="+995 599 00 11 22",
            author_id="owner2",
            author_listings_count=1,
        )
        self.db.save_listings_batch([target_broker, same_apt_owner, different_floor_apt])

        report = run_xray_on_db(self.db, "https://www.myhome.ge/ka/pr/2001/iyideba-bina/")
        self.assertIsNotNone(report)
        self.assertIn("🕵️‍♂️ სავარაუდო ფარული მაკლერი / სააგენტო", report)
        self.assertIn("🎯 <b>იგივე ბინა სხვაგან:</b>", report)
        # Must include the $79,000 owner duplicate (-$6,000 cheaper)
        self.assertIn("$79,000", report)
        self.assertIn("-$6,000", report)
        # Must NOT include the floor 3 apartment ($75,000)
        self.assertNotIn("$75,000", report)

    def test_initial_price_history_and_district_market_benchmark(self):
        # First seen at $95,000
        self.db.save_listing(
            _make_listing("4001", price_usd=95000.0, area_m2=60.0, district="საბურთალო")
        )
        # Price drops to $88,000
        self.db.save_listing(
            _make_listing("4001", price_usd=88000.0, area_m2=60.0, district="საბურთალო")
        )
        # Add another Saburtalo listing so district benchmark has multiple samples
        self.db.save_listing(
            _make_listing("4002", price_usd=102000.0, area_m2=60.0, district="საბურთალო", phone_number="+995 591 00 00 02")
        )

        report = run_xray_on_db(self.db, "https://www.myhome.ge/ka/pr/4001/iyideba-bina/")
        self.assertIsNotNone(report)
        self.assertIn("პირვანდელი ფასი: <b>$95,000</b>", report)
        self.assertIn("დაკლებულია: <b>-$7,000", report)
        self.assertIn("უბნის საშუალო ფასი", report)

    def test_facebook_text_xray_finds_matching_portal_listing(self):
        # Real owner listing exists in DB at $78,000 on floor 7
        owner_listing = _make_listing(
            "5001",
            source="myhome",
            price_usd=78000.0,
            area_m2=60.0,
            district="საბურთალო",
            floor="7",
            rooms=3,
            is_owner=True,
            phone_number="+995 599 55 44 33",
        )
        # Broker also has 3 other listings in DB under phone 577 99 88 77
        b1 = _make_listing("601", area_m2=45.0, district="ვაკე", phone_number="+995 577 99 88 77", is_owner=False)
        b2 = _make_listing("602", area_m2=90.0, district="ისანი", phone_number="+995 577 99 88 77", is_owner=False)
        self.db.save_listings_batch([owner_listing, b1, b2])

        # User copies Facebook post text from a broker asking $83,000 with phone 577998877
        fb_post = (
            "იყიდება ბინა საბურთალოზე! 60 კვ.მ, მე-7 სართული, 3 ოთახი. "
            "ევრორემონტით, ფასი 83000$. დარეკეთ 577 99 88 77"
        )
        report = run_xray_on_db(self.db, fb_post)
        self.assertIsNotNone(report)
        self.assertIn("🕵️‍♂️ სავარაუდო ფარული მაკლერი / სააგენტო", report)
        self.assertIn("$78,000", report)
        self.assertIn("-$5,000", report)


if __name__ == "__main__":
    unittest.main()
