"""Tests for the Duplicate Listing & Agent Network Detector (v2: SQL-based, dHash)."""

import asyncio
import sys
import os
import tempfile

# Ensure UTF-8 output on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import PropertyListing
from core.dedup import dhash, hamming, DuplicateDetector
from core.database import DatabaseEngine


def _make_listing(**overrides) -> PropertyListing:
    """Helper to create a PropertyListing with sensible defaults."""
    defaults = {
        "id": "test_1",
        "source": "myhome",
        "source_id": "1",
        "title": "ბინა ისანში",
        "price_usd": 60000,
        "area_m2": 55,
        "url": "https://myhome.ge/ka/pr/1",
        "district": "ისანი",
        "floor": "5",
        "rooms": 2,
    }
    defaults.update(overrides)
    return PropertyListing(**defaults)


def _insert_fake_hashes(db: DatabaseEngine, listing_id: str, hashes: list):
    """Pre-populates image_hashes table (avoids network calls in tests)."""
    with db._connection() as conn:
        cursor = conn.cursor()
        for idx, h in enumerate(hashes):
            cursor.execute(
                "INSERT OR REPLACE INTO image_hashes (listing_id, image_index, phash) VALUES (?, ?, ?)",
                (listing_id, idx, h),
            )
        conn.commit()


def _run(coro):
    """Runs async code in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --- dHash Unit Tests ---


def test_dhash_deterministic():
    """Same image data should produce same hash."""
    from PIL import Image

    img = Image.new("RGB", (100, 100), color=(128, 128, 128))
    h1 = dhash(img)
    h2 = dhash(img)
    assert h1 == h2
    assert len(h1) == 16  # 64-bit → 16 hex chars
    print("✅ test_dhash_deterministic PASSED")


def test_hamming_identical():
    """Hamming distance of identical hashes is 0."""
    assert hamming("abcdef0123456789", "abcdef0123456789") == 0
    print("✅ test_hamming_identical PASSED")


def test_hamming_different():
    """Completely different hashes have high Hamming distance."""
    d = hamming("ffffffffffffffff", "0000000000000000")
    assert d == 64
    print("✅ test_hamming_different PASSED")


def test_hamming_one_bit():
    """One bit difference."""
    d = hamming("0000000000000000", "0000000000000001")
    assert d == 1
    print("✅ test_hamming_one_bit PASSED")


# --- Group Growth Test (3 listings → count 3) ---


def test_group_grows_to_3():
    """Three duplicate listings should form a group with count=3."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DatabaseEngine(os.path.join(tmpdir, "test.db"))
        detector = DuplicateDetector(db=db, hash_threshold=10)

        MATCHING_HASH = "abcdef0123456789"
        MATCHING_HASH_2 = "abcdef0123456780"

        listings = [
            _make_listing(
                id=f"myhome_{i}",
                source_id=str(i),
                price_usd=60000 + i * 2000,
                district="ისანი",
                floor="5",
                rooms=2,
                area_m2=55,
                url=f"https://myhome.ge/{i}",
                phone_number=f"+995 555 {i:02d} 00 00",
                images=[],
            )
            for i in range(1, 4)
        ]

        # Save and check one by one (real pipeline order)
        db.save_listing(listings[0])
        _insert_fake_hashes(db, listings[0].id, [MATCHING_HASH, MATCHING_HASH_2])
        result1 = _run(detector.check_listing(listings[0]))
        assert result1 is None, "First listing → no candidates in DB yet"

        db.save_listing(listings[1])
        _insert_fake_hashes(db, listings[1].id, [MATCHING_HASH, MATCHING_HASH_2])
        result2 = _run(detector.check_listing(listings[1]))
        assert result2 is not None, "Second listing → duplicate detected"
        assert result2["duplicate_count"] == 2

        db.save_listing(listings[2])
        _insert_fake_hashes(db, listings[2].id, [MATCHING_HASH, MATCHING_HASH_2])
        result3 = _run(detector.check_listing(listings[2]))
        assert result3 is not None, "Third listing → duplicate detected"
        assert result3["duplicate_count"] == 3, f"Expected 3, got {result3['duplicate_count']}"

        print("✅ test_group_grows_to_3 PASSED")


# --- Self-Match Prevention ---


def test_no_self_match():
    """Re-submitting the same listing should not create a self-duplicate."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DatabaseEngine(os.path.join(tmpdir, "test.db"))
        detector = DuplicateDetector(db=db, hash_threshold=10)

        listing = _make_listing(
            id="myhome_1",
            source_id="1",
            price_usd=60000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/1",
            images=[],
        )
        db.save_listing(listing)
        _insert_fake_hashes(db, listing.id, ["abcdef0123456789"])

        result1 = _run(detector.check_listing(listing))
        assert result1 is None

        # Re-submit same listing
        result2 = _run(detector.check_listing(listing))
        assert result2 is None, "Same listing should not self-match"

        print("✅ test_no_self_match PASSED")


# --- Area Boundary Test ---


def test_area_boundary_same_block():
    """Listings at 48.9 and 49.1 m² should be in same candidate range (±15%)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DatabaseEngine(os.path.join(tmpdir, "test.db"))
        detector = DuplicateDetector(db=db, hash_threshold=10)

        HASH = "1234567890abcdef"
        HASH2 = "1234567890abcde0"  # 4-bit diff

        listing_a = _make_listing(
            id="myhome_1",
            source_id="1",
            price_usd=60000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=48.9,
            url="https://myhome.ge/1",
            images=[],
        )
        listing_b = _make_listing(
            id="myhome_2",
            source_id="2",
            price_usd=62000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=49.1,
            url="https://myhome.ge/2",
            images=[],
        )

        db.save_listing(listing_a)
        db.save_listing(listing_b)
        _insert_fake_hashes(db, listing_a.id, [HASH, HASH2])
        _insert_fake_hashes(db, listing_b.id, [HASH, HASH2])

        _run(detector.check_listing(listing_a))
        result_b = _run(detector.check_listing(listing_b))

        assert result_b is not None, "48.9 and 49.1 should match within ±15% area range"
        print("✅ test_area_boundary_same_block PASSED")


# --- Different Apartments Same Building ---


def test_different_apartments_same_building():
    """Two apartments in same building (same district/floor) but different images → no match."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DatabaseEngine(os.path.join(tmpdir, "test.db"))
        detector = DuplicateDetector(db=db, hash_threshold=10)

        listing_a = _make_listing(
            id="myhome_1",
            source_id="1",
            price_usd=60000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/1",
            images=[],
        )
        listing_b = _make_listing(
            id="myhome_2",
            source_id="2",
            price_usd=62000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=56,
            url="https://myhome.ge/2",
            images=[],
        )

        db.save_listing(listing_a)
        db.save_listing(listing_b)
        # Completely different image hashes
        _insert_fake_hashes(db, listing_a.id, ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"])
        _insert_fake_hashes(db, listing_b.id, ["0000000000000000", "1111111111111111"])

        _run(detector.check_listing(listing_a))
        result_b = _run(detector.check_listing(listing_b))

        assert result_b is None, "Different images → should not match even in same building"
        print("✅ test_different_apartments_same_building PASSED")


# --- 1 Photo Match Only (without price/area confirmation) → No Match ---


def test_single_photo_match_without_price_confirmation():
    """1 matching photo but vastly different price → should NOT match (AND, not OR)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DatabaseEngine(os.path.join(tmpdir, "test.db"))
        detector = DuplicateDetector(db=db, hash_threshold=10)

        listing_a = _make_listing(
            id="myhome_1",
            source_id="1",
            price_usd=60000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/1",
            images=[],
        )
        listing_b = _make_listing(
            id="myhome_2",
            source_id="2",
            price_usd=120000,  # 100% price diff
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/2",
            images=[],
        )

        db.save_listing(listing_a)
        db.save_listing(listing_b)
        # Only 1 matching photo, 1 different
        _insert_fake_hashes(db, listing_a.id, ["abcdef0123456789", "1111111111111111"])
        _insert_fake_hashes(db, listing_b.id, ["abcdef0123456789", "0000000000000000"])

        _run(detector.check_listing(listing_a))
        result_b = _run(detector.check_listing(listing_b))

        assert result_b is None, "1 photo + huge price diff → should not match"
        print("✅ test_single_photo_match_without_price_confirmation PASSED")


# --- 1 Photo + Price/Area Confirmation → Match ---


def test_single_photo_match_with_price_confirmation():
    """1 matching photo + similar price/area → should match."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DatabaseEngine(os.path.join(tmpdir, "test.db"))
        detector = DuplicateDetector(db=db, hash_threshold=10)

        listing_a = _make_listing(
            id="myhome_1",
            source_id="1",
            price_usd=60000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/1",
            images=[],
        )
        listing_b = _make_listing(
            id="myhome_2",
            source_id="2",
            price_usd=65000,  # ~8% diff
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=56,  # ~2% diff
            url="https://myhome.ge/2",
            images=[],
        )

        db.save_listing(listing_a)
        db.save_listing(listing_b)
        # 1 matching photo, 1 different
        _insert_fake_hashes(db, listing_a.id, ["abcdef0123456789", "1111111111111111"])
        _insert_fake_hashes(db, listing_b.id, ["abcdef0123456789", "0000000000000000"])

        _run(detector.check_listing(listing_a))
        result_b = _run(detector.check_listing(listing_b))

        assert result_b is not None, "1 photo + similar price → should match"
        print("✅ test_single_photo_match_with_price_confirmation PASSED")


# --- Best Price Detection ---


def test_best_price_is_lowest():
    """Group info should report the lowest price as best."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DatabaseEngine(os.path.join(tmpdir, "test.db"))
        detector = DuplicateDetector(db=db, hash_threshold=10)

        HASH = "abcdef0123456789"
        HASH2 = "abcdef0123456780"

        listing_a = _make_listing(
            id="m_1",
            source_id="1",
            price_usd=70000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/1",
            images=[],
        )
        listing_b = _make_listing(
            id="m_2",
            source_id="2",
            price_usd=58000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/2",
            images=[],
        )

        db.save_listing(listing_a)
        db.save_listing(listing_b)
        _insert_fake_hashes(db, listing_a.id, [HASH, HASH2])
        _insert_fake_hashes(db, listing_b.id, [HASH, HASH2])

        _run(detector.check_listing(listing_a))
        result = _run(detector.check_listing(listing_b))

        assert result is not None
        assert result["best_price_usd"] == 58000
        assert result["is_new_best_price"] is True
        print("✅ test_best_price_is_lowest PASSED")


# --- New Best Price Flag ---


def test_not_new_best_price():
    """Listing with higher price than existing best should flag is_new_best_price=False."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DatabaseEngine(os.path.join(tmpdir, "test.db"))
        detector = DuplicateDetector(db=db, hash_threshold=10)

        HASH = "abcdef0123456789"
        HASH2 = "abcdef0123456780"

        listing_cheap = _make_listing(
            id="m_1",
            source_id="1",
            price_usd=50000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/1",
            images=[],
        )
        listing_expensive = _make_listing(
            id="m_2",
            source_id="2",
            price_usd=70000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/2",
            images=[],
        )

        db.save_listing(listing_cheap)
        db.save_listing(listing_expensive)
        _insert_fake_hashes(db, listing_cheap.id, [HASH, HASH2])
        _insert_fake_hashes(db, listing_expensive.id, [HASH, HASH2])

        _run(detector.check_listing(listing_cheap))
        result = _run(detector.check_listing(listing_expensive))

        assert result is not None
        assert result["is_new_best_price"] is False, "Expensive listing should not be new best"
        print("✅ test_not_new_best_price PASSED")


# --- Agent Network Detection ---


def test_agent_network():
    """Agent with multiple listings should appear in agent network."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DatabaseEngine(os.path.join(tmpdir, "test.db"))
        detector = DuplicateDetector(db=db, hash_threshold=10)

        agent_phone = "+995 555 11 22 33"

        # Same agent lists 3 DIFFERENT properties (different districts)
        listings = [
            _make_listing(
                id=f"myhome_{i}",
                source_id=str(i),
                price_usd=50000 + i * 5000,
                district=d,
                floor=str(i + 1),
                rooms=2,
                area_m2=45 + i * 10,
                url=f"https://myhome.ge/{i}",
                phone_number=agent_phone,
                images=[],
            )
            for i, d in enumerate(["ისანი", "დიდუბე", "ჩუღურეთი"])
        ]

        for listing in listings:
            db.save_listing(listing)
            _run(detector.check_listing(listing))

        network = detector.get_agent_network()
        assert agent_phone in network
        assert len(network[agent_phone]) == 3
        print("✅ test_agent_network PASSED")


# --- Owner Heuristic: Phone Frequency ---


def test_owner_by_phone_frequency():
    """Phone with fewer total listings should be identified as probable owner."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DatabaseEngine(os.path.join(tmpdir, "test.db"))
        detector = DuplicateDetector(db=db, hash_threshold=10)

        HASH = "abcdef0123456789"
        HASH2 = "abcdef0123456780"

        owner_phone = "+995 555 99 99 99"  # Appears on only 1 listing
        agent_phone = "+995 555 11 11 11"  # Will appear on multiple

        # Agent has many other listings (inflates phone count)
        for i in range(5):
            extra = _make_listing(
                id=f"extra_{i}",
                source_id=str(100 + i),
                price_usd=40000 + i * 1000,
                district="დიდუბე",
                floor=str(i),
                rooms=1,
                area_m2=30 + i,
                url=f"https://myhome.ge/extra/{i}",
                phone_number=agent_phone,
                images=[],
            )
            db.save_listing(extra)
            _run(detector.check_listing(extra))

        # Now the duplicate pair
        listing_owner = _make_listing(
            id="m_owner",
            source_id="200",
            price_usd=60000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/200",
            phone_number=owner_phone,
            images=[],
        )
        listing_agent = _make_listing(
            id="m_agent",
            source_id="201",
            price_usd=65000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/201",
            phone_number=agent_phone,
            images=[],
        )

        db.save_listing(listing_owner)
        db.save_listing(listing_agent)
        _insert_fake_hashes(db, listing_owner.id, [HASH, HASH2])
        _insert_fake_hashes(db, listing_agent.id, [HASH, HASH2])

        _run(detector.check_listing(listing_owner))
        result = _run(detector.check_listing(listing_agent))

        assert result is not None
        assert result["probable_owner_phone"] == owner_phone, (
            f"Expected owner phone {owner_phone}, got {result['probable_owner_phone']}"
        )
        print("✅ test_owner_by_phone_frequency PASSED")


# --- Different Districts → No Match ---


def test_different_districts_no_match():
    """Properties in different districts should never match."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DatabaseEngine(os.path.join(tmpdir, "test.db"))
        detector = DuplicateDetector(db=db, hash_threshold=10)

        listing_a = _make_listing(
            id="m_1",
            source_id="1",
            price_usd=60000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/1",
            images=[],
        )
        listing_b = _make_listing(
            id="m_2",
            source_id="2",
            price_usd=60000,
            district="ვაკე",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/2",
            images=[],
        )

        db.save_listing(listing_a)
        db.save_listing(listing_b)
        _insert_fake_hashes(db, listing_a.id, ["abcdef0123456789", "abcdef0123456780"])
        _insert_fake_hashes(db, listing_b.id, ["abcdef0123456789", "abcdef0123456780"])

        _run(detector.check_listing(listing_a))
        result_b = _run(detector.check_listing(listing_b))

        assert result_b is None, "Different districts → no match"
        print("✅ test_different_districts_no_match PASSED")


# --- Duplicate Stats ---


def test_duplicate_stats():
    """get_duplicate_stats should report correct counts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DatabaseEngine(os.path.join(tmpdir, "test.db"))
        detector = DuplicateDetector(db=db, hash_threshold=10)

        HASH = "abcdef0123456789"
        HASH2 = "abcdef0123456780"

        listing1 = _make_listing(
            id="m_1",
            source_id="1",
            price_usd=60000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/1",
            images=[],
        )
        listing2 = _make_listing(
            id="s_2",
            source="ss_ge",
            source_id="2",
            price_usd=62000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://ss.ge/2",
            images=[],
        )

        db.save_listing(listing1)
        db.save_listing(listing2)
        _insert_fake_hashes(db, listing1.id, [HASH, HASH2])
        _insert_fake_hashes(db, listing2.id, [HASH, HASH2])

        _run(detector.check_listing(listing1))
        _run(detector.check_listing(listing2))

        stats = detector.get_duplicate_stats()
        assert stats["duplicate_groups"] >= 1
        assert stats["total_duplicate_listings"] >= 2
        print("✅ test_duplicate_stats PASSED")


# --- No Images → No False Positive ---


def test_no_images_no_match():
    """Listings without images should never be matched as duplicates."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = DatabaseEngine(os.path.join(tmpdir, "test.db"))
        detector = DuplicateDetector(db=db, hash_threshold=10)

        listing_a = _make_listing(
            id="m_1",
            source_id="1",
            price_usd=60000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/1",
            images=[],
        )
        listing_b = _make_listing(
            id="m_2",
            source_id="2",
            price_usd=60000,
            district="ისანი",
            floor="5",
            rooms=2,
            area_m2=55,
            url="https://myhome.ge/2",
            images=[],
        )

        db.save_listing(listing_a)
        db.save_listing(listing_b)
        # No image hashes inserted

        _run(detector.check_listing(listing_a))
        result_b = _run(detector.check_listing(listing_b))

        assert result_b is None, "No images → no matching should occur"
        print("✅ test_no_images_no_match PASSED")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Running Duplicate Detector Tests (v2)")
    print("=" * 60 + "\n")

    test_dhash_deterministic()
    test_hamming_identical()
    test_hamming_different()
    test_hamming_one_bit()
    test_group_grows_to_3()
    test_no_self_match()
    test_area_boundary_same_block()
    test_different_apartments_same_building()
    test_single_photo_match_without_price_confirmation()
    test_single_photo_match_with_price_confirmation()
    test_best_price_is_lowest()
    test_not_new_best_price()
    test_agent_network()
    test_owner_by_phone_frequency()
    test_different_districts_no_match()
    test_duplicate_stats()
    test_no_images_no_match()

    print("\n" + "=" * 60)
    print("  ✅ All 17 tests PASSED")
    print("=" * 60 + "\n")
