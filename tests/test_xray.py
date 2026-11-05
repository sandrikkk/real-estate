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

        res_tbilisi = parse_xray_input("იყიდება ბინა თბილისში 60 კვ.მ, ფასი 85000$")
        self.assertIsNone(res_tbilisi["district"], "'თბილისში' must not resolve to ლისი")
        res_lisi = parse_xray_input("იყიდება ბინა ლისზე 60 კვ.მ, ფასი 85000$")
        self.assertEqual(res_lisi["district"], "ლისი")
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

    def test_ss_ge_36062395_agent_not_reported_as_owner(self):
        # Reproduces the exact production bug:
        # SS.ge listing 36062395 ($57,000, 40 m², თემქა, phone +995 577 95 49 66)
        # saved from search page with is_owner=None, user_type=None, author_id=None
        unverified_ss = _make_listing(
            "36062395",
            source="ss_ge",
            price_usd=57000.0,
            area_m2=40.0,
            district="თემქა",
            floor="4",
            rooms=1,
            is_owner=None,
            user_type=None,
            phone_number="+995 577 95 49 66",
            author_id=None,
            author_listings_count=None,
        )
        unverified_ss.url = (
            "https://home.ss.ge/ka/udzravi-qoneba/iyideba-1-otaxiani-bina-temqaze-36062395"
        )
        self.db.save_listings_batch([unverified_ss])

        # 1. Even without live_details, an unverified (is_owner=None) row must NOT claim "✅ რეალური მესაკუთრე (Owner)"
        report_unverified = run_xray_on_db(self.db, "+995 577 95 49 66")
        self.assertIsNotNone(report_unverified)
        self.assertNotIn("✅ რეალური მესაკუთრე (Owner)", report_unverified)
        self.assertIn("დაუზუსტებელია", report_unverified)

        # 2. When live_details from SS.ge reveals "აგენტი" (is_owner=False, user_type="agent"),
        # both phone X-Ray and URL X-Ray must flag it as a broker!
        live_ss_agent = {
            "is_owner": False,
            "user_type": "agent",
            "author_id": "ss_agent_77",
            "author_listings_count": 1,
            "phone_number": "+995 577 95 49 66",
        }
        report_phone_enriched = run_xray_on_db(
            self.db, "+995 577 95 49 66", live_details=live_ss_agent
        )
        self.assertIn("🕵️‍♂️ სავარაუდო ფარული მაკლერი / სააგენტო", report_phone_enriched)
        self.assertNotIn("✅ რეალური მესაკუთრე (Owner)", report_phone_enriched)

        report_url_enriched = run_xray_on_db(
            self.db,
            "https://home.ss.ge/ka/udzravi-qoneba/iyideba-1-otaxiani-bina-temqaze-36062395",
            live_details=live_ss_agent,
        )
        self.assertIn("🕵️‍♂️ სავარაუდო ფარული მაკლერი / სააგენტო", report_url_enriched)
        self.assertNotIn("✅ რეალური მესაკუთრე (Owner)", report_url_enriched)

    def test_myhome_masked_phone_xray_and_owner_duplicate_discovery(self):
        # Reproduces the exact production Bug 2 reported by the user:
        # MyHome masks phone numbers as "579882***" for +995 579 88 21 47.
        # In D1, agent has 3 listings with phone_number="579882***", and
        # myhome_26573206 is a duplicate of owner listing myhome_26444531 ($73,000, is_owner=True).
        mh_agent_1 = _make_listing(
            "26573206",
            source="myhome",
            price_usd=72500.0,
            area_m2=51.0,
            district="გლდანი",
            floor="5",
            rooms=2,
            is_owner=False,
            user_type="agent",
            phone_number="579882***",
            author_id=None,
            author_listings_count=None,
            group_id="myhome_26444531",
        )
        mh_agent_2 = _make_listing(
            "26544171",
            source="myhome",
            price_usd=75000.0,
            area_m2=42.0,
            district="გლდანი",
            floor="6",
            rooms=2,
            is_owner=False,
            user_type="agent",
            phone_number="579882***",
            author_id=None,
            author_listings_count=None,
            group_id="myhome_25735194",
        )
        mh_agent_3 = _make_listing(
            "26571202",
            source="myhome",
            price_usd=74000.0,
            area_m2=52.0,
            district="თემქა",
            floor="3",
            rooms=3,
            is_owner=False,
            user_type="agent",
            phone_number="579882***",
            author_id=None,
            author_listings_count=None,
            group_id="myhome_26520053",
        )
        mh_real_owner = _make_listing(
            "26444531",
            source="myhome",
            price_usd=73000.0,
            area_m2=51.0,
            district="გლდანი",
            floor="5",
            rooms=2,
            is_owner=True,
            user_type="physical",
            phone_number=None,
            author_id=None,
            author_listings_count=None,
            group_id="myhome_26444531",
        )
        self.db.save_listings_batch([mh_agent_1, mh_agent_2, mh_agent_3, mh_real_owner])

        # 1. Querying by full unmasked phone (+995 579 88 21 47) must match "579882***"
        report_full = run_xray_on_db(self.db, "+995 579 88 21 47")
        self.assertIsNotNone(report_full)
        self.assertNotIn("არ ფიქსირდება", report_full)
        self.assertIn("🕵️‍♂️ სავარაუდო ფარული მაკლერი / სააგენტო", report_full)
        self.assertIn("$72,500", report_full)
        self.assertIn("$75,000", report_full)
        self.assertIn("$74,000", report_full)
        # Must also surface the real owner duplicate (myhome_26444531 at $73,000)
        self.assertIn("$73,000", report_full)
        self.assertIn("მესაკუთრე ✅", report_full)

        # 2. Querying by masked phone ("579882***" copied directly from bot alert) must also work
        report_masked = run_xray_on_db(self.db, "579882***")
        self.assertIsNotNone(report_masked)
        self.assertNotIn("არ ფიქსირდება", report_masked)
        self.assertIn("🕵️‍♂️ სავარაუდო ფარული მაკლერი / სააგენტო", report_masked)
        self.assertIn("$72,500", report_masked)

    def test_exact_phone_takes_precedence_over_masked_prefix_collision(self):
        # Unrelated agent has "579882***" (e.g. 579 88 21 47)
        agent_masked = _make_listing(
            "9001",
            source="myhome",
            price_usd=72500.0,
            area_m2=51.0,
            district="გლდანი",
            is_owner=False,
            user_type="agent",
            phone_number="579882***",
        )
        # Verified physical owner has exact phone "+995 579 88 29 99" (same 579882 prefix!)
        exact_owner = _make_listing(
            "9002",
            source="ss_ge",
            price_usd=65000.0,
            area_m2=48.0,
            district="ვაკე",
            is_owner=True,
            user_type="physical",
            phone_number="+995 579 88 29 99",
            author_id="real_owner_99",
            author_listings_count=1,
        )
        self.db.save_listings_batch([agent_masked, exact_owner])

        report = run_xray_on_db(self.db, "+995 579 88 29 99")
        self.assertIsNotNone(report)
        self.assertIn("✅ რეალური მესაკუთრე (Owner)", report)
        self.assertIn("$65,000", report)
        self.assertNotIn("$72,500", report)

    def test_cloudflare_worker_xray_integration(self):
        worker_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "cloudflare",
            "worker.js",
        )
        with open(worker_path, "r", encoding="utf-8") as f:
            worker_src = f.read()

        # 1. handleMessage receives env from handleTelegramWebhook
        self.assertIn("await handleMessage(update.message, token, kv, env);", worker_src)
        self.assertIn("async function handleMessage(msg, token, kv, env)", worker_src)

        # 2. awaiting_price and awaiting_area state handlers precede handleXRayQuery
        idx_price = worker_src.find('user.state === "awaiting_price"')
        idx_area = worker_src.find('user.state === "awaiting_area"')
        idx_xray = worker_src.find("await handleXRayQuery(text, env)")
        self.assertGreater(idx_price, 0)
        self.assertGreater(idx_area, idx_price)
        self.assertGreater(idx_xray, idx_area)

        # 3. Core X-Ray functions defined in cloudflare/worker.js
        self.assertIn("function parseXRayInput(text)", worker_src)
        self.assertIn("async function fetchLiveStatementDetails(", worker_src)
        self.assertIn("async function handleXRayQuery(text, env)", worker_src)

        # 4. Uses indexed phone_number IN (?, ?, ?) with maskedPhone and never unindexed LIKE '%...'
        self.assertIn("phone_number IN (?, ?, ?)", worker_src)
        self.assertIn("maskedPhone", worker_src)
        self.assertNotIn("phone_number LIKE '%", worker_src)

        # 5. MyHome dehydratedState searches all queries (not hardcoded queries?.[0])
        self.assertNotIn("queries?.[0]?.state?.data?.data?.statement", worker_src)
        self.assertIn("function parseMyHomeStatementDetails(", worker_src)
        self.assertIn("function extractMaskedPhone(", worker_src)
        self.assertIn("phoneDupMap", worker_src)

        # 6. SS.ge agent detection checks userEntityType, initialAgentInfoData, applicationPhones, and api-gateway.ss.ge
        self.assertIn("userEntityType", worker_src)
        self.assertIn("initialAgentInfoData", worker_src)
        self.assertIn("applicationPhones", worker_src)
        self.assertIn("api-gateway.ss.ge/v1/RealEstate/details", worker_src)

        # 7. Balanced braces check
        self.assertEqual(worker_src.count("{"), worker_src.count("}"))

        # 8. Missing SYNC_KEY fails closed on every protected route
        self.assertIn("if (!expectedKey) return false;", worker_src)
        self.assertNotIn("if (!expectedKey) return true;", worker_src)
        self.assertEqual(worker_src.count("isSyncKeyValid(request, env)"), 5)

    def test_ss_ge_scraper_statement_details_parses_broker_signals(self):
        from scrapers.ss_ge import SSGeScraper

        scraper = SSGeScraper()
        exact_36062395_payload = {
            "applicationId": 36062395,
            "userId": "d44c353e-7ebe-4bcf-ac38-4de1f259bf6f",
            "userEntityType": "Broker",
            "userApplicationCount": 474,
            "companyName": "city home",
            "companyId": 2201,
            "applicationPhones": [{"phoneNumber": "+995-577-95-49-66"}],
            "price": {"priceUsd": 57000, "priceGeo": 155000},
            "totalArea": 40,
            "floorNumber": "4",
            "numberOfRooms": 1,
        }
        parsed = scraper._parse_statement_details_data(
            exact_36062395_payload,
            {"companyId": 2201, "companyType": 2, "agentContactName": "ლიკა"},
        )
        self.assertIsNotNone(parsed)
        self.assertIs(parsed["is_owner"], False)
        self.assertEqual(parsed["author_listings_count"], 474)
        self.assertEqual(parsed["phone_number"], "+995 577 95 49 66")

    def test_worker_auto_deploy_security_and_fail_closed(self):
        from pathlib import Path
        from scripts.deploy_worker import (
            extract_worker_name,
            parse_bindings_from_cf_settings,
            render_wrangler_toml,
        )

        # 1. Worker name extraction from CLOUDFLARE_PROXY_URL
        self.assertEqual(
            extract_worker_name("", "https://soft-smoke-33e0.iashvilisandro7.workers.dev"),
            "soft-smoke-33e0",
        )
        self.assertEqual(
            extract_worker_name("custom-worker", "https://soft-smoke-33e0.iashvilisandro7.workers.dev"),
            "custom-worker",
        )

        # 2. Binding discovery from Cloudflare Worker settings API payload
        sample_cf_settings = {
            "success": True,
            "result": {
                "bindings": [
                    {"name": "USERS_KV", "type": "kv_namespace", "namespace_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90"},
                    {"name": "DB", "type": "d1", "id": "11112222-3333-4444-5555-666677778888", "database_name": "real-estate-db"},
                    {"name": "TELEGRAM_BOT_TOKEN", "type": "secret_text"},
                ]
            },
        }
        bindings = parse_bindings_from_cf_settings(sample_cf_settings)
        self.assertEqual(bindings["kv_namespace_id"], "a1b2c3d4e5f60718293a4b5c6d7e8f90")
        self.assertEqual(bindings["d1_database_id"], "11112222-3333-4444-5555-666677778888")
        self.assertEqual(bindings["d1_database_name"], "real-estate-db")

        # 3. Fail-closed validation: missing or unsafe IDs must raise ValueError before wrangler runs
        with self.assertRaises(ValueError):
            render_wrangler_toml("soft-smoke-33e0", "", "11112222-3333-4444-5555-666677778888")
        with self.assertRaises(ValueError):
            render_wrangler_toml("soft-smoke-33e0", "a1b2c3d4e5f60718293a4b5c6d7e8f90", "")
        with self.assertRaises(ValueError):
            render_wrangler_toml("soft-smoke-33e0", "YOUR_KV_NAMESPACE_ID", "11112222-3333-4444-5555-666677778888")

        # 4. Safe TOML generation preserves vars & bindings
        toml_text = render_wrangler_toml(
            "soft-smoke-33e0",
            "a1b2c3d4e5f60718293a4b5c6d7e8f90",
            "11112222-3333-4444-5555-666677778888",
            "real-estate-db",
        )
        self.assertIn('name = "soft-smoke-33e0"', toml_text)
        self.assertIn("keep_vars = true", toml_text)
        self.assertIn('binding = "USERS_KV"', toml_text)
        self.assertIn('binding = "DB"', toml_text)
        self.assertIn('compatibility_date = "2026-08-22"', toml_text)

        # wrangler@3 exits silently with code 1 locally, so discovery and deploy must use v4
        deploy_src = (
            Path(__file__).resolve().parent.parent / "scripts" / "deploy_worker.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("wrangler@3", deploy_src)
        self.assertIn("wrangler@4", deploy_src)

        # 5. Repository hygiene: wrangler.toml is gitignored and no live wrangler.toml exists on disk
        repo_root = Path(__file__).resolve().parent.parent
        gitignore_text = (repo_root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("cloudflare/wrangler.toml", gitignore_text)
        self.assertIn(".dev.vars", gitignore_text)
        self.assertFalse((repo_root / "cloudflare" / "wrangler.toml").exists())
        self.assertTrue((repo_root / "cloudflare" / "wrangler.toml.example").exists())
        self.assertTrue((repo_root / ".github" / "workflows" / "deploy-worker.yml").exists())


if __name__ == "__main__":
    unittest.main()
