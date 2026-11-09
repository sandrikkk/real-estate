import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from core.database import DatabaseEngine
from core.district_prices import parse_price_table
from core.models import PropertyListing
from core.xray import (
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
    street: str = None,
    total_floors: int = None,
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
        total_floors=total_floors,
        rooms=rooms,
        district=district,
        street=street,
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


def _myhome_price_provider(asked: list):
    """Stands in for MyHome's price statistics (real 2-bedroom response, 2026-10-07)."""
    with open(
        os.path.join(os.path.dirname(__file__), "fixtures", "myhome_district_prices_bed2.json"),
        encoding="utf-8",
    ) as f:
        table = parse_price_table(json.load(f))

    def provider(bedroom_type_id):
        asked.append(bedroom_type_id)
        return table

    return provider


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
        res_mh = parse_xray_input(
            "ნახე ეს ბინა https://www.myhome.ge/ka/pr/22051234/iyideba-3-otaxiani-bina/"
        )
        self.assertIsNotNone(res_mh)
        self.assertEqual(res_mh["kind"], "url")
        self.assertEqual(res_mh["source"], "myhome")
        self.assertEqual(res_mh["source_id"], "22051234")
        self.assertEqual(res_mh["listing_id"], "myhome_22051234")

        # 2. SS.ge URL
        res_ss = parse_xray_input(
            "https://home.ss.ge/ka/udzravi-qoneba/iyideba-3-otaxiani-bina-saburtaloze-31948512"
        )
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
            "იყიდება საბურთალოზე 60 კვ.მ ბინა, მე-7 სართული, 3 ოთახი, ფასი 85000$, ტელ 599112233"
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
                _make_listing(
                    "101", area_m2=55.0, district="საბურთალო", phone_number="+995 599 11 22 33"
                ),
                _make_listing(
                    "102", area_m2=82.0, district="ვაკე", phone_number="+995 599 11 22 33"
                ),
                _make_listing(
                    "103", area_m2=110.0, district="დიღომი", phone_number="+995 599 11 22 33"
                ),
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

        asked: list = []
        report = run_xray_on_db(
            self.db,
            "https://www.myhome.ge/ka/pr/4001/iyideba-bina/",
            price_table_provider=_myhome_price_provider(asked),
        )
        self.assertIsNotNone(report)
        self.assertIn("პირვანდელი ფასი: <b>$95,000</b>", report)
        self.assertIn("დაკლებულია: <b>-$7,000", report)
        # MyHome's 2-bedroom Saburtalo figure (1577.81), not an average of our own rows;
        # 3 rooms = 2 bedrooms. $88,000 / 60 m² = $1,467/m² is 7% below it.
        self.assertIn(
            "📊 <b>უბნის საშუალო ფასი (საბურთალო):</b> $1,578/მ² (-7% საბაზროსთან)", report
        )
        self.assertEqual(asked, [2])

    def test_district_benchmark_is_omitted_when_myhome_has_no_figure(self):
        self.db.save_listing(
            _make_listing("4101", price_usd=88000.0, area_m2=60.0, district="საბურთალო")
        )
        # Rows of our own in the same district must not stand in for the missing figure
        self.db.save_listing(
            _make_listing(
                "4102",
                price_usd=102000.0,
                area_m2=60.0,
                district="საბურთალო",
                phone_number="+995 591 00 00 02",
            )
        )
        url = "https://www.myhome.ge/ka/pr/4101/iyideba-bina/"

        no_provider = run_xray_on_db(self.db, url)
        self.assertNotIn("უბნის საშუალო ფასი", no_provider)

        api_down = run_xray_on_db(self.db, url, price_table_provider=lambda _bedroom: None)
        self.assertNotIn("უბნის საშუალო ფასი", api_down)

        self.db.save_listing(
            _make_listing("4103", price_usd=88000.0, area_m2=60.0, district="არარსებული უბანი")
        )
        unknown = run_xray_on_db(
            self.db,
            "https://www.myhome.ge/ka/pr/4103/iyideba-bina/",
            price_table_provider=_myhome_price_provider([]),
        )
        self.assertNotIn("უბნის საშუალო ფასი", unknown)

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
        b1 = _make_listing(
            "601", area_m2=45.0, district="ვაკე", phone_number="+995 577 99 88 77", is_owner=False
        )
        b2 = _make_listing(
            "602", area_m2=90.0, district="ისანი", phone_number="+995 577 99 88 77", is_owner=False
        )
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
        idx_xray = worker_src.find("await handleXRayQuery(text, env, { chatId, collect })")
        self.assertGreater(idx_price, 0)
        self.assertGreater(idx_area, idx_price)
        self.assertGreater(idx_xray, idx_area)

        # 3. Core X-Ray functions defined in cloudflare/worker.js
        self.assertIn("function parseXRayInput(text)", worker_src)
        self.assertIn("async function fetchLiveStatementDetails(", worker_src)
        self.assertIn("async function handleXRayQuery(text, env, opts = {})", worker_src)

        # 4. Uses indexed phone_number IN (?, ?, ?) with maskedPhone and never unindexed LIKE '%...'
        self.assertIn("phone_number IN (?, ?, ?)", worker_src)
        self.assertIn("maskedPhone", worker_src)
        self.assertNotIn("phone_number LIKE '%", worker_src)

        # 5. MyHome dehydratedState searches all queries (not hardcoded queries?.[0])
        self.assertNotIn("queries?.[0]?.state?.data?.data?.statement", worker_src)
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
        # the definition, plus proxy, /api/debug, /api/users, /api/db/query, /api/xray/followup
        self.assertEqual(worker_src.count("isSyncKeyValid(request, env)"), 6)

        # 9. MyHome blocks the Worker (ADR 0004): the Worker reads only SS.ge listings live,
        # and MyHome owner fields come from the lookup job (scripts/xray_lookup.py).
        self.assertIn('if (source !== "ss_ge") return null;', worker_src)
        self.assertNotIn("api-statements.tnet.ge/v1/statements/${sourceId}", worker_src)
        self.assertIn('"X-Website-Key": "myhome"', worker_src)
        self.assertIn("formatOwnerBadge(", worker_src)

    def test_owner_with_60m2_and_61m2_cross_portal_listings_not_flagged_as_broker(self):
        # Real owner listed the same apartment on MyHome as 60 m² and on SS.ge as 61 m²
        mh_owner = _make_listing(
            "8201",
            source="myhome",
            price_usd=80000.0,
            area_m2=60.0,
            district="საბურთალო",
            floor="7",
            rooms=3,
            is_owner=True,
            user_type="physical",
            phone_number="+995 599 44 55 66",
            author_id="owner_same_apt",
            author_listings_count=1,
        )
        ss_owner = _make_listing(
            "8202",
            source="ss_ge",
            price_usd=79500.0,
            area_m2=61.0,
            district="ნუცუბიძე",
            floor="7",
            rooms=3,
            is_owner=True,
            user_type="physical",
            phone_number="+995 599 44 55 66",
            author_id="ss_owner_same_apt",
            author_listings_count=1,
        )
        self.db.save_listings_batch([mh_owner, ss_owner])

        report = run_xray_on_db(self.db, "+995 599 44 55 66")
        self.assertIsNotNone(report)
        self.assertIn("✅ რეალური მესაკუთრე (Owner)", report)
        self.assertNotIn("🕵️‍♂️ სავარაუდო ფარული მაკლერი / სააგენტო", report)

    def test_phoneless_fb_post_subdistrict_alias_limit_bypass_and_phone_recovery(self):
        # 30 cheap rent/other-floor 60 m² listings in "საბურთალო" that previously exhausted LIMIT 25
        noise_listings = [
            _make_listing(
                f"rent_{i}",
                source="ss_ge",
                price_usd=500.0 + i * 10,
                area_m2=60.0,
                district="საბურთალო",
                floor="2",
                rooms=2,
                is_owner=True,
                phone_number=f"+995 555 10 10 {i:02d}",
            )
            for i in range(30)
        ]
        # Real owner listing on MyHome saved under parent district "საბურთალო" on floor 7
        owner_listing = _make_listing(
            "7001",
            source="myhome",
            price_usd=78000.0,
            area_m2=60.0,
            district="საბურთალო",
            floor="7",
            rooms=3,
            is_owner=True,
            user_type="physical",
            phone_number="+995 599 55 44 33",
            author_id="owner_7001",
            author_listings_count=1,
        )
        self.db.save_listings_batch(noise_listings + [owner_listing])

        # Phoneless FB post using subdistrict "ნუცუბიძეზე"
        fb_post_no_phone = (
            "იყიდება ბინა ნუცუბიძეზე! 60 კვ.მ, მე-7 სართული, 3 ოთახი. "
            "სუფთა რემონტით, ფასი 83000$. მომწერეთ პირადში"
        )
        report = run_xray_on_db(self.db, fb_post_no_phone)
        self.assertIsNotNone(report)
        # Must match the MyHome listing in "საბურთალო" despite "ნუცუბიძე" vs "საბურთალო" and 30 cheaper rows
        self.assertIn("$78,000", report)
        self.assertIn("-$5,000", report)
        # Must surface the recovered phone number from the portal duplicate
        self.assertIn("+995 599 55 44 33", report)
        # Verdict must reflect the matched owner listing rather than a bogus "1 განცხადება" on fb_input
        self.assertIn("✅ რეალური მესაკუთრე (Owner)", report)

    def test_phoneless_fb_post_with_no_match_reports_missing_phone_honestly(self):
        fb_post_no_phone = "იყიდება ბინა ვარკეთილში, 64 კვ.მ, მე-9 სართული, 3 ოთახი, ფასი 71000$"
        report = run_xray_on_db(self.db, fb_post_no_phone)
        self.assertIsNotNone(report)
        self.assertNotIn("ბაზაში ფიქსირდება 1 განცხადება", report)
        self.assertIn("ტელეფონის ნომერი არ წერია", report)

    def test_fb_post_without_floor_does_not_match_arbitrary_floors_at_different_prices(self):
        # Different apartments of 60 m² in Saburtalo at very different prices/rooms
        unrelated_floor_2 = _make_listing(
            "8001",
            source="ss_ge",
            price_usd=62000.0,
            area_m2=60.0,
            district="საბურთალო",
            floor="2",
            rooms=2,
            is_owner=True,
            phone_number="+995 599 00 00 01",
        )
        unrelated_floor_14 = _make_listing(
            "8002",
            source="myhome",
            price_usd=135000.0,
            area_m2=60.0,
            district="საბურთალო",
            floor="14",
            rooms=3,
            is_owner=True,
            phone_number="+995 599 00 00 02",
        )
        # Likely same apartment: 60 m², 3 rooms, $86,000 (within 15% of $88,000)
        close_match = _make_listing(
            "8003",
            source="myhome",
            price_usd=86000.0,
            area_m2=60.0,
            district="საბურთალო",
            floor="5",
            rooms=3,
            is_owner=True,
            phone_number="+995 599 00 00 03",
        )
        self.db.save_listings_batch([unrelated_floor_2, unrelated_floor_14, close_match])

        # FB post omits floor
        fb_post_no_floor = "იყიდება ბინა საბურთალოზე, 60 კვ.მ, 3 ოთახი, ფასი 88000$"
        report = run_xray_on_db(self.db, fb_post_no_floor)
        self.assertIsNotNone(report)
        self.assertNotIn("$62,000", report)
        self.assertNotIn("$135,000", report)
        self.assertIn("$86,000", report)
        self.assertIn("+995 599 00 00 03", report)

    def test_unverified_duplicate_not_labeled_as_agency(self):
        target_broker = _make_listing(
            "8101",
            source="myhome",
            price_usd=90000.0,
            area_m2=65.0,
            district="ვაკე",
            floor="4",
            rooms=3,
            is_owner=False,
            user_type="agent",
            phone_number="+995 555 12 12 12",
        )
        unverified_dup = _make_listing(
            "8102",
            source="ss_ge",
            price_usd=87000.0,
            area_m2=65.0,
            district="ვაკე",
            floor="4",
            rooms=3,
            is_owner=None,
            user_type=None,
            phone_number=None,
        )
        self.db.save_listings_batch([target_broker, unverified_dup])

        report = run_xray_on_db(self.db, "https://www.myhome.ge/ka/pr/8101/iyideba-bina/")
        self.assertIsNotNone(report)
        self.assertIn("$87,000", report)
        self.assertIn("გადაუმოწმებელი ⚠️", report)

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
            extract_worker_name(
                "custom-worker", "https://soft-smoke-33e0.iashvilisandro7.workers.dev"
            ),
            "custom-worker",
        )

        # 2. Binding discovery from Cloudflare Worker settings API payload
        sample_cf_settings = {
            "success": True,
            "result": {
                "bindings": [
                    {
                        "name": "USERS_KV",
                        "type": "kv_namespace",
                        "namespace_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
                    },
                    {
                        "name": "DB",
                        "type": "d1",
                        "id": "11112222-3333-4444-5555-666677778888",
                        "database_name": "real-estate-db",
                    },
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
            render_wrangler_toml(
                "soft-smoke-33e0", "YOUR_KV_NAMESPACE_ID", "11112222-3333-4444-5555-666677778888"
            )

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
        # Workers Logs keeps console.error lines; without it a missing secret leaves no trace
        import tomllib

        self.assertIs(tomllib.loads(toml_text)["observability"]["enabled"], True)

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

    def test_broker_verdict_clustering_respects_rooms_and_merges_60_61_m2(self):
        from core.xray import evaluate_broker_verdict

        # 1. Same owner posts on MyHome (60 m², floor 5, 2 rooms) and SS.ge (61 m², floor 5, 2 rooms)
        r1 = {
            "id": "mh_1",
            "district": "საბურთალო",
            "area_m2": 60.0,
            "floor": "5/12",
            "rooms": 2,
            "is_owner": 1,
            "user_type": "physical",
        }
        r2 = {
            "id": "ss_1",
            "district": "საბურთალო",
            "area_m2": 61.0,
            "floor": "5",
            "rooms": 2,
            "is_owner": 1,
            "user_type": "physical",
        }
        verdict_same = evaluate_broker_verdict(r1, [r1, r2])
        self.assertFalse(verdict_same["is_broker"])
        self.assertEqual(verdict_same["unique_apartments"], 1)

        # 2. Different room counts on same floor (e.g. 2 rooms vs 3 rooms, 60 m² vs 61 m²) -> 2 apartments (broker)
        r3 = {
            "id": "ss_2",
            "district": "საბურთალო",
            "area_m2": 61.0,
            "floor": "5",
            "rooms": 3,
            "is_owner": 1,
            "user_type": "physical",
        }
        verdict_diff_rooms = evaluate_broker_verdict(r1, [r1, r3])
        self.assertTrue(verdict_diff_rooms["is_broker"])
        self.assertEqual(verdict_diff_rooms["unique_apartments"], 2)

    def test_the_facebook_post_is_not_counted_among_the_listings_found_under_its_number(self):
        from core.xray import evaluate_broker_verdict

        # Production, 2026-11-08: "2 listings (2 different flats)" counted the post itself.
        post = {
            "id": "fb_input",
            "district": "გლდანი",
            "area_m2": 72.0,
            "floor": "1",
            "rooms": 3,
            "phone_number": "+995 555 00 01 11",
        }
        other = {
            "id": "myhome_3",
            "district": "გლდანი",
            "area_m2": 110.0,
            "floor": "7",
            "rooms": 4,
            "is_owner": 0,
            "user_type": "agent",
        }
        verdict = evaluate_broker_verdict(post, [other])
        self.assertTrue(verdict["is_broker"])
        self.assertIn(
            "კიდევ <b>1 განცხადება</b> (ამ პოსტთან ერთად 2 სხვადასხვა ბინა)", verdict["reason"]
        )
        self.assertNotIn("<b>2 განცხადება</b>", verdict["reason"])

        same_flat = dict(other, area_m2=71.0, floor="1", rooms=3)
        again = evaluate_broker_verdict(post, [same_flat])
        self.assertIn("კიდევ <b>1 განცხადება</b> (იგივე ბინა)", again["reason"])

        # Without a post in the rows, the count and wording are the old ones.
        listing = evaluate_broker_verdict(other, [dict(other, id="myhome_4", area_m2=50.0)])
        self.assertIn("იძებნება <b>2 განცხადება</b> (2 სხვადასხვა ბინა)", listing["reason"])

    def test_worker_webhook_secret_stack_scrubbing_and_scheduled_trigger(self):
        from pathlib import Path

        worker_path = Path(__file__).resolve().parent.parent / "cloudflare" / "worker.js"
        worker_src = worker_path.read_text(encoding="utf-8")

        # 1. Webhook verifies X-Telegram-Bot-Api-Secret-Token when TELEGRAM_WEBHOOK_SECRET is configured
        self.assertIn("X-Telegram-Bot-Api-Secret-Token", worker_src)
        self.assertIn("TELEGRAM_WEBHOOK_SECRET", worker_src)

        # 2. handleApiUsers must not leak fatalErr.stack in JSON responses
        self.assertNotIn("fatalErr.stack", worker_src)

        # 3. Worker exports scheduled() handler to trigger GitHub workflow_dispatch when configured
        self.assertIn("async scheduled(", worker_src)
        self.assertIn("dispatches", worker_src)


# Correct values out of 100 posts (price: out of the USD-priced posts) on the golden set.
# A ratchet: raise a number when the parser improves, never lower it. The numbers live in
# tests/fixtures/xray_golden_baseline.json, which tests/js/xray_worker.test.mjs reads too.
with open(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "fixtures", "xray_golden_baseline.json"
    ),
    encoding="utf-8",
) as _f:
    GOLDEN_BASELINE = json.load(_f)

# Incident 2026-11-06 (docs/production-fixes.md §5.5), FB text reconstructed.
INCIDENT_FB = (
    "იყიდება ქალაქური პროექტის ბინა თემქაზე, 3 მ/რ, 4 კვარტალი. 62 კვ.მ, 1 სართული 8 სართულიანი კორპუსის. "
    "ერთი საძინებელი, მისაღები და ლოჯი. ფასი 79 000$ ტელ: 599 90 90 01"
)


def _incident_nadzaladevi_listing() -> PropertyListing:
    return _make_listing(
        "26524985",
        price_usd=92000.0,
        area_m2=63.0,
        district="ნაძალადევი",
        floor="1",
        rooms=4,
        phone_number="+995 555 00 00 02",
        author_id="a1",
        author_listings_count=2,
    ).model_copy(update={"street": "ზედაზნის ქ. 6", "total_floors": 12, "bedrooms": 3})


class TestXRayFalseDuplicateIncident(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = DatabaseEngine(
            db_path=os.path.join(self.temp_dir.name, "test_xray_incident.db"), use_d1=False
        )
        self.db.init_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_golden_fb_posts_parser_accuracy_never_drops_below_baseline(self):
        import json

        fixture = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fixtures", "xray_fb_posts.json"
        )
        with open(fixture, encoding="utf-8") as f:
            posts = json.load(f)

        def norm(v):
            return None if v in (None, "") else v

        def same(got, want):
            got, want = norm(got), norm(want)
            if got is None or want is None:
                return got is want
            if isinstance(want, (int, float)) and not isinstance(want, bool):
                return float(got) == float(want)
            return str(got) == str(want)

        correct: dict = {}
        for post in posts:
            e = post["expected"] or {}
            r = parse_xray_input(post["text"]) or {}
            pairs = {
                "kind": (r.get("kind"), e.get("xray_kind")),
                "area_m2": (r.get("area_m2"), e.get("area_m2")),
                "floor": (r.get("floor"), e.get("floor")),
                "rooms": (r.get("rooms"), e.get("rooms")),
                "bedrooms": (r.get("bedrooms"), e.get("bedrooms")),
                "total_floors": (r.get("total_floors"), e.get("total_floors")),
                "district": (r.get("district"), e.get("district")),
                "phone9": (r.get("phone_9"), e.get("phone9")),
            }
            if e.get("currency") == "USD":
                pairs["price_usd"] = (r.get("price_usd"), e.get("price"))
            for field, (got, want) in pairs.items():
                if same(got, want):
                    correct[field] = correct.get(field, 0) + 1
        for field, minimum in GOLDEN_BASELINE.items():
            self.assertGreaterEqual(correct.get(field, 0), minimum, field)

    def test_incident_temqa_post_does_not_match_4_room_nadzaladevi_flat(self):
        self.db.save_listings_batch([_incident_nadzaladevi_listing()])
        report = run_xray_on_db(self.db, INCIDENT_FB)
        self.assertIsNotNone(report)
        self.assertNotIn("26524985", report)
        self.assertIn("დუბლიკატი სხვა პორტალზე არ იძებნება", report)

    def test_incident_post_still_finds_its_real_duplicate(self):
        same_flat = _make_listing(
            "777",
            price_usd=81000.0,
            area_m2=62.0,
            district="ნაძალადევი",
            floor="1/8",
            rooms=2,
            phone_number="+995 599 77 77 77",
        ).model_copy(update={"subdistrict": "თემქა", "total_floors": 8, "bedrooms": 1})
        self.db.save_listings_batch([_incident_nadzaladevi_listing(), same_flat])
        report = run_xray_on_db(self.db, INCIDENT_FB)
        self.assertIn("/pr/777/", report)
        self.assertNotIn("26524985", report)

    def test_matcher_area_and_floor_alone_are_not_enough(self):
        from core.xray import _candidate_matches_target

        target = {"district": "თემქა", "area_m2": 62, "floor": "1", "rooms": 0, "price_usd": 79000}
        cand = {
            "district": "ნაძალადევი",
            "area_m2": 62,
            "floor": "1",
            "rooms": None,
            "price_usd": 79000,
        }
        self.assertFalse(_candidate_matches_target(target, cand))

    def test_matcher_known_layout_or_height_conflicts_reject(self):
        from core.xray import _candidate_matches_target

        base = {"district": "საბურთალო", "area_m2": 60, "floor": "7"}
        self.assertFalse(_candidate_matches_target({**base, "bedrooms": 1}, {**base, "rooms": 4}))
        self.assertFalse(
            _candidate_matches_target(
                {**base, "rooms": 3, "total_floors": 8}, {**base, "rooms": 3, "floor": "7/12"}
            )
        )

    def test_matcher_corroborated_duplicates_still_match(self):
        from core.xray import _candidate_matches_target

        base = {"area_m2": 60, "floor": "7"}
        self.assertTrue(
            _candidate_matches_target(
                {**base, "district": "ნუცუბიძე", "rooms": 3},
                {**base, "district": "საბურთალო", "rooms": 3},
            )
        )
        self.assertTrue(
            _candidate_matches_target(
                {**base, "district": "საბურთალო", "bedrooms": 2},
                {**base, "district": "საბურთალო", "rooms": 3},
            )
        )
        self.assertTrue(
            _candidate_matches_target(
                {**base, "district": "თემქა"},
                {**base, "district": "ნაძალადევი", "subdistrict": "თემქა"},
            )
        )
        self.assertTrue(
            _candidate_matches_target(
                {**base, "district": "საბურთალო", "rooms": 3, "total_floors": 12},
                {**base, "district": "საბურთალო", "rooms": 3, "total_floors": 13},
            )
        )

    def test_parser_reads_bedrooms_and_building_height(self):
        cases = [
            ("2 საძინებელი, 65 კვ.მ", {"bedrooms": 2}),
            ("ორი ნათელი საძინებლით, 65 კვ.მ", {"bedrooms": 2}),
            ("ერთი საძინებლით, 45 კვ.მ", {"bedrooms": 1}),
            ("65 კვ.მ, სართული: 7/25", {"floor": "7", "total_floors": 25}),
            ("65 კვ.მ, 1-ლი სართული 5-სართულიან კორპუსში", {"floor": "1", "total_floors": 5}),
            ("65 კვ.მ, 2-ე სართული (9) სართულიანია", {"floor": "2", "total_floors": 9}),
            ("65 კვ.მ, 8-სართულიანი კორპუსის მე-6 სართული", {"floor": "6", "total_floors": 8}),
        ]
        for text, want in cases:
            got = parse_xray_input(f"იყიდება ბინა საბურთალოზე {text}") or {}
            for field, value in want.items():
                self.assertEqual(got.get(field), value, f"{field} in {text!r}")

    def test_merge_live_answer_replaces_stored_and_full_phone_replaces_masked(self):
        from core.xray import _merge_live_into_row

        healed = _merge_live_into_row(
            {"is_owner": 0, "user_type": "agent", "phone_number": "555000***"},
            {"is_owner": 1, "user_type": "physical", "phone_number": "+995 555 00 00 02"},
        )
        self.assertEqual(healed["is_owner"], 1)
        self.assertEqual(healed["user_type"], "physical")
        self.assertEqual(healed["phone_number"], "+995 555 00 00 02")

        agent = _merge_live_into_row(
            {"is_owner": 1, "user_type": "physical"}, {"is_owner": 0, "user_type": "agent"}
        )
        self.assertEqual(agent["is_owner"], 0)
        self.assertEqual(agent["user_type"], "agent")

        unknown = _merge_live_into_row(
            {"is_owner": 0, "phone_number": "+995 555 00 00 02"},
            {"is_owner": None, "phone_number": "555000***"},
        )
        self.assertEqual(unknown["is_owner"], 0)
        self.assertEqual(unknown["phone_number"], "+995 555 00 00 02")

    def test_find_exact_duplicates_with_weights_and_fallback(self):
        target = {
            "id": "myhome_t",
            "district": "გლდანი",
            "micro": 6,
            "floor": "1",
            "area_m2": 72.0,
            "rooms": 3,
            "total_floors": 9,
            "price_usd": 60000.0,
            "deal_type": "sale",
        }
        cand_agree = _make_listing(
            "cand_1",
            source="ss_ge",
            price_usd=60000.0,
            area_m2=72.0,
            district="გლდანი",
            street="გლდანი - VI მ/რ",
            floor="1",
            rooms=3,
            total_floors=9,
        )
        cand_only_fa = _make_listing(
            "cand_2",
            source="ss_ge",
            price_usd=60000.0,
            area_m2=72.0,
            district="გლდანი",
            street="",
            floor="1",
            rooms=None,
        )
        self.db.save_listings_batch([cand_agree, cand_only_fa])

        sample_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tests",
            "fixtures",
            "xray_weights_sample.json",
        )
        with open(sample_path, encoding="utf-8") as f:
            sample_weights = json.load(f)

        import sqlite3

        conn = sqlite3.connect(self.db.db_path)
        try:
            dups_scored = find_exact_duplicates(conn, target, weights=sample_weights)
            self.assertEqual(len(dups_scored), 1)
            self.assertEqual(dups_scored[0]["id"], "ss_ge_cand_1")
            self.assertIn("xray_posterior", dups_scored[0])
            self.assertGreater(dups_scored[0]["xray_posterior"], 0.5)
            self.assertEqual(dups_scored[0]["xray_weights"], "w-2026-W46")

            dups_fallback = find_exact_duplicates(conn, target, weights=None)
            self.assertEqual(len(dups_fallback), 1)
            self.assertEqual(dups_fallback[0]["id"], "ss_ge_cand_1")
            self.assertNotIn("xray_posterior", dups_fallback[0])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
