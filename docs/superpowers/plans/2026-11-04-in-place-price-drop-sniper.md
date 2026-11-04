# In-Place Price Drop Sniper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect and alert when an existing listing on MyHome.ge, SS.ge, or Area.ge lowers its price in-place on the same listing ID, with zero extra Cloudflare D1 row reads.

**Architecture:** `DatabaseEngine` piggybacks on the existing `SELECT ... FROM properties WHERE id IN (...)` batch primary-key lookup (plus an in-memory `_seen_prices` cache) to classify scraped listings as either genuinely new or price-dropped (`scraped.price_usd < stored.price_usd`). `RealEstateOrchestrator.run_cycle` routes price-dropped listings through hygiene, subscriber matching, and live detail enrichment (re-verifying the live price is still strictly lower than `previous_price_usd`), updates the stored price in `properties`, and sends a Telegram alert displaying the previous price and exact dollar drop.

**Tech Stack:** Python 3.11+, SQLite / Cloudflare D1 HTTP batch proxy, Pydantic v2, `unittest`.

**Spec:** [`docs/superpowers/specs/2026-11-04-in-place-price-drop-sniper-design.md`](../specs/2026-11-04-in-place-price-drop-sniper-design.md)

## Global Constraints

- Cloudflare D1 bound parameters per query must never exceed 50 (`CHUNK_SIZE = 50`).
- Zero extra D1 `SELECT` queries or row reads when existing listings have unchanged or higher prices.
- `DatabaseEngine.filter_new_listings(listing_ids: list) -> list` must remain backward-compatible for existing callers and tests.
- All currency values in Markdown documentation must stay inside backticks (e.g., `` `$70,000` ``).

## Review Focus

1. **Same-price or higher-price re-scrapes (`scraped.price_usd >= stored.price_usd`):** Must be skipped immediately with 0 detail enrichment calls, 0 DB writes, and 0 alerts.
2. **Post-enrichment false price drop (`live_usd >= previous_price_usd`):** If the search page showed a lower price (e.g., `$68,000`) but `fetch_statement_details` returns the unchanged live price (`$70,000`), the orchestrator must cancel the price-drop flag and not re-alert already-notified subscribers.
3. **Previously notified subscriber receiving a price drop:** A subscriber who already has `(chat_id, listing.id)` in `user_notifications` from the initial `$70,000` alert MUST still receive the alert when the listing drops to `$65,000`, and MUST NOT receive duplicate alerts on subsequent cycles at `$65,000`.
4. **Listing dropping into subscriber budget for the first time:** A listing initially saved as unmatched at `$75,000` (above a subscriber's `$72,000` max budget) that later drops to `$68,000` must match and alert that subscriber.
5. **Combined in-place price drop + duplicate cluster:** When a price-dropped listing also belongs to a duplicate group (`dup_info`), `TelegramNotifier.format_message` must not render duplicate `📉 წინა ფასი:` lines.

---

### Task 1: Model Fields & Database Batch Price-Drop Classification

**Files:**
- Modify: `core/models.py:130-140`
- Modify: `core/database.py:155-165,484-510,566-655`
- Test: `tests/test_batch_db.py`

**Interfaces:**
- Consumes: `PropertyListing` from `core/models.py`
- Produces:
  - `PropertyListing.previous_price_usd: Optional[float] = None`
  - `PropertyListing.price_drop_usd: Optional[float] = None`
  - `DatabaseEngine.classify_scraped_listings(self, listings: list[PropertyListing]) -> tuple[list[PropertyListing], dict[str, float]]`
    - Returns `(candidate_listings, old_prices_by_id)` where `candidate_listings` contains genuinely new listings plus existing listings whose `listing.price_usd > 0` and `listing.price_usd < stored_price_usd`. For price-dropped listings, `old_prices_by_id[listing.id] = stored_price_usd`, and `listing.previous_price_usd` / `listing.price_drop_usd` are populated on the `PropertyListing` instance.

- [x] **Step 1: Write the failing test in `tests/test_batch_db.py`**

```python
    def test_classify_scraped_listings_detects_new_and_price_drops_only(self):
        l1 = PropertyListing(
            id="myhome_1",
            source="myhome",
            source_id="1",
            title="Apt 1",
            price_usd=70000.0,
            area_m2=55.0,
            url="https://myhome.ge/1",
        )
        l2 = PropertyListing(
            id="myhome_2",
            source="myhome",
            source_id="2",
            title="Apt 2",
            price_usd=65000.0,
            area_m2=50.0,
            url="https://myhome.ge/2",
        )
        self.db.save_listings_batch([l1, l2])

        # Cold-start simulation (clear in-memory caches as in stateless CI)
        self.db._seen_ids.clear()
        self.db._seen_prices.clear()

        l1_dropped = PropertyListing(
            id="myhome_1",
            source="myhome",
            source_id="1",
            title="Apt 1",
            price_usd=69999.0,  # $1 drop
            area_m2=55.0,
            url="https://myhome.ge/1",
        )
        l2_same = PropertyListing(
            id="myhome_2",
            source="myhome",
            source_id="2",
            title="Apt 2",
            price_usd=65000.0,  # unchanged
            area_m2=50.0,
            url="https://myhome.ge/2",
        )
        l3_new = PropertyListing(
            id="myhome_3",
            source="myhome",
            source_id="3",
            title="Apt 3",
            price_usd=60000.0,  # brand new
            area_m2=48.0,
            url="https://myhome.ge/3",
        )

        candidates, price_drops = self.db.classify_scraped_listings([l1_dropped, l2_same, l3_new])
        self.assertEqual([c.id for c in candidates], ["myhome_1", "myhome_3"])
        self.assertEqual(price_drops, {"myhome_1": 70000.0})
        self.assertEqual(l1_dropped.previous_price_usd, 70000.0)
        self.assertEqual(l1_dropped.price_drop_usd, 1.0)
        self.assertIsNone(l3_new.previous_price_usd)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_batch_db.py -k test_classify_scraped_listings_detects_new_and_price_drops_only`
Expected: FAIL with `AttributeError: 'DatabaseEngine' object has no attribute '_seen_prices'` or `'classify_scraped_listings'`

- [x] **Step 3: Implement model fields in `core/models.py` and `classify_scraped_listings` in `core/database.py`**

In `core/models.py`, add optional fields to `PropertyListing`:
```python
    previous_price_usd: Optional[float] = None
    price_drop_usd: Optional[float] = None
```
In `DatabaseEngine.__init__`, initialize `self._seen_prices: Dict[str, float] = {}`. Update `save_listing` and `save_listings_batch` to set `self._seen_prices[listing.id] = float(listing.price_usd)` when `listing.price_usd` is present. Implement `classify_scraped_listings(self, listings: list) -> tuple[list, dict]` using `SELECT id, price_usd FROM properties WHERE id IN (...)` chunked by `CHUNK_SIZE = 50` for any listing ID not already in `self._seen_prices`.

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_batch_db.py`
Expected: PASS (`OK`)

- [x] **Step 5: Commit**

```bash
git add core/models.py core/database.py tests/test_batch_db.py
git commit -m "feat(db): classify in-place price drops during batch primary-key lookup"
```

---

### Task 2: Telegram Message Formatting for In-Place Price Drops

**Files:**
- Modify: `notifier/telegram_bot.py:170-204`
- Test: `tests/test_telegram_format.py`

**Interfaces:**
- Consumes: `PropertyListing.previous_price_usd`, `PropertyListing.price_drop_usd` from Task 1
- Produces: Updated `TelegramNotifier.format_message(self, listing: PropertyListing, dup_info: Optional[dict] = None) -> str`
  - When `listing.previous_price_usd` is set and `listing.previous_price_usd > listing.price_usd` (and not already rendered by `dup_info.get("is_strictly_lower")`), appends:
    - `🔻 <b>ფასი დააკლდა!</b>`
    - `📉 წინა ფასი: <b>{prev_str}</b> | დაკლებულია: <b>-{drop_str}</b>`

- [x] **Step 1: Write the failing test in `tests/test_telegram_format.py`**

```python
    def test_in_place_price_drop_formatting_and_no_double_line_with_dup_info(self):
        listing = PropertyListing(
            id="myhome_5555",
            source="myhome",
            source_id="5555",
            title="იყიდება ბინა საბურთალოზე",
            price_usd=64999.0,
            previous_price_usd=65000.0,
            price_drop_usd=1.0,
            area_m2=50.0,
            rooms=2,
            district="საბურთალო",
            url="https://www.myhome.ge/ka/pr/5555",
            is_owner=True,
        )
        msg = self.notifier.format_message(listing)
        self.assertIn("🔻 <b>ფასი დააკლდა!</b>", msg)
        self.assertIn("📉 წინა ფასი: <b>$65,000</b> | დაკლებულია: <b>-$1</b>", msg)

        # When dup_info also has is_strictly_lower=True, "📉 წინა ფასი:" must appear only once
        dup_info = {
            "duplicate_count": 2,
            "is_strictly_lower": True,
            "previous_best_price_usd": 65000.0,
            "price_drop_usd": 1.0,
            "best_price_usd": 64999.0,
            "best_price_url": "https://www.myhome.ge/ka/pr/5555",
        }
        msg_with_dup = self.notifier.format_message(listing, dup_info=dup_info)
        self.assertEqual(msg_with_dup.count("📉 წინა ფასი:"), 1)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_telegram_format.py -k test_in_place_price_drop_formatting_and_no_double_line_with_dup_info`
Expected: FAIL with `AssertionError: '🔻 <b>ფასი დააკლდა!</b>' not found`

- [x] **Step 3: Implement in-place price-drop banner in `notifier/telegram_bot.py`**

In `TelegramNotifier.format_message`, if `getattr(listing, "previous_price_usd", None)` is strictly greater than `listing.price_usd` and not already displayed via `dup_info.get("is_strictly_lower")`, append the `🔻 <b>ფასი დააკლდა!</b>` header and `📉 წინა ფასი: <b>{prev_str}</b> | დაკლებულია: <b>-{drop_str}</b>` line.

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_telegram_format.py`
Expected: PASS (`OK`)

- [x] **Step 5: Commit**

```bash
git add notifier/telegram_bot.py tests/test_telegram_format.py
git commit -m "feat(notifier): render in-place price drop banner with previous price and delta"
```

---

### Task 3: Orchestrator Pipeline Integration for Price-Dropped Listings

**Files:**
- Modify: `main.py:223-440`
- Test: `tests/test_fixes.py`

**Interfaces:**
- Consumes:
  - `DatabaseEngine.classify_scraped_listings` from Task 1
  - `TelegramNotifier.format_message` from Task 2
- Produces:
  - Updated `RealEstateOrchestrator.run_cycle` in `main.py`:
    - Uses `candidates, price_drops = self.db.classify_scraped_listings(unique_listings)`.
    - Allows price-dropped listings (`listing.id in price_drops`) to match subscribers even if `listing.id` was previously notified at the older, higher price.
    - After `fetch_statement_details`, re-computes `listing.price_drop_usd = round(old_price - listing.price_usd, 2)` if `listing.price_usd < old_price`; if `listing.price_usd >= old_price` (false drop), clears `listing.previous_price_usd = None`, `listing.price_drop_usd = None`, removes `listing.id` from `price_drops`, and filters `matching_users` back to only unnotified users.
    - Saves the updated price via `self.db.save_listing(listing)` (or `save_listings_batch` for unmatched drops) so subsequent cycles see the new price and do not re-alert.

- [x] **Step 1: Write the failing tests in `tests/test_fixes.py`**

```python
    async def test_run_cycle_alerts_on_in_place_price_drop_once_and_skips_false_drop(self):
        """Verify that an existing listing dropping price in-place alerts subscribers
        (even if previously notified at higher price), updates DB so next cycle is silent,
        and ignores false price drops when live detail price did not actually drop."""
        from main import RealEstateOrchestrator

        orch = RealEstateOrchestrator()
        orch.db = self.db
        orch.telegram_notifier = MagicMock()
        orch.telegram_notifier.send_notification = AsyncMock(return_value=True)

        user = UserSubscription(
            chat_id="u_1",
            price_min_usd=50000,
            price_max_usd=72000,
            area_min_m2=45,
            area_max_m2=65,
            rooms_min=2,
            districts=["ისანი"],
            owner_type="all",
        )

        # Cycle 1: Listing 1 at $70,000 (notified to u_1) and Listing 2 at $75,000 (above u_1 max price, saved unmatched)
        l1_v1 = PropertyListing(
            id="myhome_901",
            source="myhome",
            source_id="901",
            title="იყიდება ბინა ისანში",
            price_usd=70000.0,
            area_m2=55.0,
            rooms=2,
            district="ისანი",
            condition_id=1,
            condition_name="ახალი გარემონტებული",
            is_owner=True,
            url="https://myhome.ge/901",
        )
        l2_v1 = PropertyListing(
            id="myhome_902",
            source="myhome",
            source_id="902",
            title="იყიდება ბინა ისანში 2",
            price_usd=75000.0,
            area_m2=55.0,
            rooms=2,
            district="ისანი",
            condition_id=1,
            condition_name="ახალი გარემონტებული",
            is_owner=True,
            url="https://myhome.ge/902",
        )

        mock_scraper = MagicMock()
        mock_scraper.name = "MyHome.ge"
        mock_scraper.fetch_listings = AsyncMock(return_value=[l1_v1, l2_v1])
        mock_scraper.fetch_statement_details = AsyncMock(return_value=None)
        orch.scrapers = [mock_scraper]

        with patch("main.fetch_active_users", return_value=[user]):
            stats1 = await orch.run_cycle()
        self.assertEqual(stats1["notifications_sent"], 1)

        # Cycle 2: l1 drops to $65,000, l2 drops to $68,000 (now inside budget!)
        l1_v2 = l1_v1.model_copy()
        l1_v2.price_usd = 65000.0
        l2_v2 = l2_v1.model_copy()
        l2_v2.price_usd = 68000.0
        mock_scraper.fetch_listings = AsyncMock(return_value=[l1_v2, l2_v2])

        with patch("main.fetch_active_users", return_value=[user]):
            stats2 = await orch.run_cycle()
        self.assertEqual(stats2["notifications_sent"], 2)

        # Verify l1_v2 had previous_price_usd=70000.0 and price_drop_usd=5000.0
        sent_listings = [call.args[0] for call in orch.telegram_notifier.send_notification.call_args_list[-2:]]
        by_id = {item.id: item for item in sent_listings}
        self.assertEqual(by_id["myhome_901"].previous_price_usd, 70000.0)
        self.assertEqual(by_id["myhome_901"].price_drop_usd, 5000.0)
        self.assertEqual(by_id["myhome_902"].previous_price_usd, 75000.0)
        self.assertEqual(by_id["myhome_902"].price_drop_usd, 7000.0)

        # Cycle 3: Same dropped prices ($65,000 and $68,000) -> 0 notifications, 0 detail calls
        mock_scraper.fetch_statement_details.reset_mock()
        l1_v3 = l1_v2.model_copy()
        l2_v3 = l2_v2.model_copy()
        mock_scraper.fetch_listings = AsyncMock(return_value=[l1_v3, l2_v3])
        with patch("main.fetch_active_users", return_value=[user]):
            stats3 = await orch.run_cycle()
        self.assertEqual(stats3["notifications_sent"], 0)
        mock_scraper.fetch_statement_details.assert_not_called()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_fixes.py -k test_run_cycle_alerts_on_in_place_price_drop_once_and_skips_false_drop`
Expected: FAIL with `AssertionError: 0 != 2` on `stats2["notifications_sent"]`

- [x] **Step 3: Implement price-drop routing in `main.py` and `save_listings_batch` price upsert in `core/database.py`**

1. In `core/database.py`, update `save_listings_batch` to upsert `price_usd` and `price_per_m2` when `excluded.price_usd < properties.price_usd` (`ON CONFLICT(id) DO UPDATE SET price_usd = excluded.price_usd, price_per_m2 = excluded.price_per_m2 WHERE excluded.price_usd < properties.price_usd`), so even unmatched listings keep their stored price current when it drops.
2. In `main.py`:
   - Replace `new_listing_ids = set(self.db.filter_new_listings(all_unique_ids))` with `new_listings, price_drops = self.db.classify_scraped_listings(unique_listings)`.
   - When building `matching_users`, allow a user to match if `listing.id in price_drops` OR `listing.id in unnotified_ids_by_user.get(str(u.chat_id), set())`.
   - After statement detail enrichment updates `listing.price_usd`, if `listing.id in price_drops`:
     - Check `old_price = price_drops[listing.id]`.
     - If `listing.price_usd < old_price`: update `listing.previous_price_usd = old_price` and `listing.price_drop_usd = round(old_price - listing.price_usd, 2)`.
     - Else (live price did not actually drop): reset `listing.previous_price_usd = None`, `listing.price_drop_usd = None`, `price_drops.pop(listing.id, None)`, and filter `matching_users` to only those with `listing.id in unnotified_ids_by_user.get(str(u.chat_id), set())`.

- [x] **Step 4: Run full test suite to verify all tests pass**

Run: `python3 -m unittest discover tests`
Expected: PASS (`OK`)

- [x] **Step 5: Commit**

```bash
git add core/database.py main.py tests/test_fixes.py docs/superpowers/specs/2026-11-04-in-place-price-drop-sniper-design.md docs/superpowers/plans/2026-11-04-in-place-price-drop-sniper.md
git commit -m "feat(orchestrator): alert subscribers on in-place listing price drops"
```
