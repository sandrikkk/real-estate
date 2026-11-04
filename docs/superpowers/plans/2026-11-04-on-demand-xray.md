# On-Demand Listing & Phone X-Ray Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable users to send a MyHome.ge/SS.ge link, a Georgian phone number, or a Facebook post text to the Telegram bot and receive an instant X-Ray report revealing hidden brokers vs real owners, cheaper duplicate listings, initial price history, and district market comparison.

**Architecture:** We extend `PropertyListing` and the `properties` schema in [`core/database.py`](file:///home/user/Desktop/real-estate/core/database.py) with `initial_price_usd`, `author_id`, and `author_listings_count` plus B-tree indexes on `phone_number` and `author_id` so lookups never perform full-table scans. Scrapers ([`scrapers/myhome.py`](file:///home/user/Desktop/real-estate/scrapers/myhome.py) and [`scrapers/ss_ge.py`](file:///home/user/Desktop/real-estate/scrapers/ss_ge.py)) populate author metadata on both search cards and detail pages. The X-Ray analyzer is implemented in [`core/xray.py`](file:///home/user/Desktop/real-estate/core/xray.py) (for Python/CLI and unit testing) and mirrored inside the single-file [`cloudflare/worker.js`](file:///home/user/Desktop/real-estate/cloudflare/worker.js) (`handleMessage` with `env.DB` + live HTML/JSON fallback) for instant 24/7 Telegram responses.

**Tech Stack:** Python 3.12+, SQLite3 / Cloudflare D1, Cloudflare Workers (ES Modules JavaScript), `unittest`

**Spec:** [`docs/superpowers/specs/2026-11-04-on-demand-xray-design.md`](file:///home/user/Desktop/real-estate/docs/superpowers/specs/2026-11-04-on-demand-xray-design.md)

## Global Constraints

- **No Full-Table Scans in D1:** Phone lookups MUST use `WHERE phone_number IN (?, ?)` with `['+995 5XX XX XX XX', '5XXXXXXXX']` backed by `idx_properties_phone`, never `LIKE '%...'`.
- **Single-File Cloudflare Worker:** [`cloudflare/worker.js`](file:///home/user/Desktop/real-estate/cloudflare/worker.js) is pasted directly into Cloudflare Dashboard; it MUST remain a single self-contained ES module with zero external local JS imports.
- **Permanent Initial Price Preservation:** `initial_price_usd` MUST be set to `price_usd` on initial `INSERT` and preserved via `COALESCE(properties.initial_price_usd, properties.price_usd, excluded.price_usd)` on `ON CONFLICT(id) DO UPDATE`.
- **User State Priority in Telegram Bot:** If a user is currently in `user.state === "awaiting_price"` or `user.state === "awaiting_area"`, numeric inputs MUST be handled by the state handler first before X-Ray detection runs.
- **Strict Duplicate Matching:** Cross-listing duplicate search (when `group_id` is absent) MUST require matching `district` AND `ABS(area_m2 - ?) <= 1.5` AND compatible `floor` / `rooms` when present on both rows, so unrelated apartments of the same size in the same district are not falsely reported as the same apartment.

## Review Focus

1. **Legacy DB rows where `initial_price_usd` is `NULL` before a price update:** When an existing row with `initial_price_usd = NULL` and `price_usd = 95000` is updated to `88000`, `COALESCE(properties.initial_price_usd, properties.price_usd, excluded.price_usd)` must lock in `95000.0` as `initial_price_usd` before `price_usd` becomes `88000.0` (tested in Task 1).
2. **Price increase after initial insert:** If a listing is inserted at `$80,000` and later updated to `$85,000`, `save_listings_batch` must update `price_usd` to `85000.0` while keeping `initial_price_usd` at `80000.0`, without triggering a false price-drop alert in `classify_scraped_listings` (tested in Task 1).
3. **Same-district, same-area (`60 m²`) apartment on a different floor (`floor="3"` vs `floor="9"`):** Must NOT be classified as an exact duplicate of the target apartment (tested in Task 3).
4. **Private (`user_type="physical"`) seller who has 3+ different apartments in DB or `author_listings_count >= 3`:** Must be flagged as `🕵️‍♂️ სავარაუდო ფარული მაკლერი` despite claiming `physical` status (tested in Task 3).
5. **User in `awaiting_price` or `awaiting_area` state sending numbers in Telegram:** `cloudflare/worker.js` must process the filter range state prior to X-Ray detection, and unrecognized random text (e.g. `"გამარჯობა"`) must still fall through to the help prompt (tested in Task 4).

---

### Task 1: Database & Model Support for `initial_price_usd`, `author_id`, `author_listings_count`, and Phone/Author Indexes

**Files:**
- Modify: `core/models.py:125-145`
- Modify: `core/database.py:275-410`
- Modify: `core/database.py:615-775`
- Test: `tests/test_batch_db.py`

**Interfaces:**
- Produces on `PropertyListing` in `core/models.py`:
  - `initial_price_usd: Optional[float] = None`
  - `author_id: Optional[str] = None`
  - `author_listings_count: Optional[int] = None`
- Produces in `properties` table in `core/database.py`:
  - Columns: `initial_price_usd REAL`, `author_id TEXT`, `author_listings_count INTEGER`
  - Indexes: `idx_properties_phone ON properties(phone_number)`, `idx_properties_author_id ON properties(author_id)`

- [x] **Step 1: Write the failing tests in `tests/test_batch_db.py`**

Add tests to `TestBatchDB` in `tests/test_batch_db.py`:

```python
    def test_initial_price_preserved_ across_price_drop_and_increase(self):
        l1 = self._make_listing("myhome_init_1", price=95000.0)
        l1.author_id = "user_777"
        l1.author_listings_count = 4
        self.db.save_listings_batch([l1])

        # Drop price to 88000
        l1_drop = self._make_listing("myhome_init_1", price=88000.0)
        self.db.save_listings_batch([l1_drop])

        with self.db._connection() as conn:
            row = conn.cursor().execute(
                "SELECT price_usd, initial_price_usd, author_id, author_listings_count FROM properties WHERE id = ?",
                ("myhome_init_1",),
            ).fetchone()
            self.assertEqual(float(row["price_usd"]), 88000.0)
            self.assertEqual(float(row["initial_price_usd"]), 95000.0)
            self.assertEqual(row["author_id"], "user_777")
            self.assertEqual(int(row["author_listings_count"]), 4)

        # Increase price to 99000 -> price_usd updates, initial_price_usd stays 95000, no sniper drop alert
        l1_inc = self._make_listing("myhome_init_1", price=99000.0)
        candidates, drops = self.db.classify_scraped_listings([l1_inc])
        self.assertEqual(len(candidates), 0)
        self.assertEqual(drops, {})
        self.db.save_listings_batch([l1_inc])

        with self.db._connection() as conn:
            row = conn.cursor().execute(
                "SELECT price_usd, initial_price_usd FROM properties WHERE id = ?",
                ("myhome_init_1",),
            ).fetchone()
            self.assertEqual(float(row["price_usd"]), 99000.0)
            self.assertEqual(float(row["initial_price_usd"]), 95000.0)

    def test_legacy_null_initial_price_backfilled_on_update(self):
        l1 = self._make_listing("myhome_legacy_1", price=100000.0)
        self.db.save_listing(l1)
        # Simulate legacy row with initial_price_usd = NULL
        with self.db._connection() as conn:
            conn.cursor().execute(
                "UPDATE properties SET initial_price_usd = NULL WHERE id = ?",
                ("myhome_legacy_1",),
            )
            conn.commit()

        l1_updated = self._make_listing("myhome_legacy_1", price=92000.0)
        self.db.save_listing(l1_updated)

        with self.db._connection() as conn:
            row = conn.cursor().execute(
                "SELECT price_usd, initial_price_usd FROM properties WHERE id = ?",
                ("myhome_legacy_1",),
            ).fetchone()
            self.assertEqual(float(row["price_usd"]), 92000.0)
            self.assertEqual(float(row["initial_price_usd"]), 100000.0)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_batch_db.py -v`
Expected: FAIL (`sqlite3.OperationalError: no such column: initial_price_usd`)

- [x] **Step 3: Implement `initial_price_usd`, `author_id`, `author_listings_count`, and indexes in `core/models.py` and `core/database.py`**

1. In [`core/models.py`](file:///home/user/Desktop/real-estate/core/models.py#L130-L142), add:
   - `initial_price_usd: Optional[float] = None`
   - `author_id: Optional[str] = None`
   - `author_listings_count: Optional[int] = None`
2. In [`core/database.py`](file:///home/user/Desktop/real-estate/core/database.py#L281-L390) (`init_db`):
   - Add `("initial_price_usd", "REAL")`, `("author_id", "TEXT")`, and `("author_listings_count", "INTEGER")` to both D1 auto-migration and local SQLite `CREATE TABLE` + `ALTER TABLE` migrations.
   - Add `CREATE INDEX IF NOT EXISTS idx_properties_phone ON properties(phone_number)` and `CREATE INDEX IF NOT EXISTS idx_properties_author_id ON properties(author_id)` in both D1 and local SQLite initialization blocks.
3. In [`core/database.py`](file:///home/user/Desktop/real-estate/core/database.py#L615-L780) (`save_listing` and `save_listings_batch`):
   - Include `initial_price_usd, author_id, author_listings_count` in the `INSERT INTO properties` column list (using `listing.initial_price_usd or listing.price_usd`, `listing.author_id`, `listing.author_listings_count`).
   - In `ON CONFLICT(id) DO UPDATE SET`:
     - Set `initial_price_usd = COALESCE(properties.initial_price_usd, properties.price_usd, excluded.price_usd)`
     - Set `author_id = COALESCE(excluded.author_id, properties.author_id)`
     - Set `author_listings_count = COALESCE(excluded.author_listings_count, properties.author_listings_count)`
   - In `save_listings_batch`: update `_seen_prices[item.id] = float(item.price_usd)` whenever `item.price_usd > 0`, and in `ON CONFLICT(id) DO UPDATE` allow updating when `excluded.price_usd > 0 AND (properties.price_usd IS NOT excluded.price_usd OR properties.phone_number IS NOT excluded.phone_number OR properties.author_id IS NOT excluded.author_id)`.

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_batch_db.py -v`
Expected: PASS (all tests in `tests/test_batch_db.py` pass)

- [x] **Step 5: Commit**

```bash
git add core/models.py core/database.py tests/test_batch_db.py
git commit -m "feat(db): add initial_price_usd, author metadata, and phone/author indexes"
```

---

### Task 2: Populate `author_id` and `author_listings_count` in Scrapers & Pipeline

**Files:**
- Modify: `scrapers/myhome.py:490-645`
- Modify: `scrapers/ss_ge.py:210-290`
- Modify: `scrapers/ss_ge.py:450-560`
- Modify: `main.py:288-375`
- Test: `tests/test_scrapers.py`

**Interfaces:**
- Consumes: `PropertyListing.author_id`, `PropertyListing.author_listings_count` from Task 1
- Produces:
  - `MyHomeScraper._normalize_item` and `fetch_statement_details` extract `author_id` (`str(user_id)`) and `author_listings_count` (`int(user_statements_count or statements_count)`).
  - `SSGeScraper._normalize_item` and `fetch_statement_details` extract `author_id` (`str(userId or agencyId)`) and `author_listings_count` (`int(userApplicationCount)`).
  - `main.py` applies `author_id` and `author_listings_count` from `details` during enrichment.

- [x] **Step 1: Write the failing test in `tests/test_scrapers.py`**

Add a test method to `tests/test_scrapers.py` verifying that `MyHomeScraper._normalize_item` extracts `author_id` and `author_listings_count` from `user_id` / `user_statements_count`, and `SSGeScraper` extracts `author_id` and `author_listings_count` from `userId` / `userApplicationCount`.

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_scrapers.py -v`
Expected: FAIL (`None != '12345'`)

- [x] **Step 3: Implement `author_id` and `author_listings_count` extraction in `scrapers/myhome.py`, `scrapers/ss_ge.py`, and `main.py`**

1. In [`scrapers/myhome.py`](file:///home/user/Desktop/real-estate/scrapers/myhome.py#L593-L641):
   - Extract `raw_uid = item.get("user_id") or (item.get("user") or {}).get("id")` -> `author_id = str(raw_uid) if raw_uid else None`.
   - Extract `raw_cnt = item.get("user_statements_count") or item.get("statements_count") or (item.get("user") or {}).get("statements_count")` -> `author_listings_count = _safe_int(raw_cnt)`.
   - Pass `author_id=author_id, author_listings_count=author_listings_count` to `PropertyListing(...)`.
2. In [`scrapers/ss_ge.py`](file:///home/user/Desktop/real-estate/scrapers/ss_ge.py#L215-L285) and `_normalize_item`:
   - In `fetch_statement_details`: include `"author_id": str(app_data.get("userId") or agency_id) if (app_data.get("userId") or agency_id) else None` and `"author_listings_count": int(app_count) if isinstance(app_count, int) else None`.
   - In `_normalize_item`: extract `raw_uid = item.get("userId") or item.get("agencyId")` and `raw_cnt = item.get("userApplicationCount")` and pass to `PropertyListing(...)`.
3. In [`main.py`](file:///home/user/Desktop/real-estate/main.py#L292-L370):
   - When enriching from `details` for MyHome and SS.ge, set `listing.author_id` and `listing.author_listings_count` if not already set.

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_scrapers.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add scrapers/myhome.py scrapers/ss_ge.py main.py tests/test_scrapers.py
git commit -m "feat(scrapers): extract author_id and author_listings_count on search and detail items"
```

---

### Task 3: Python X-Ray Engine (`core/xray.py`) & Unit Tests (`tests/test_xray.py`)

**Files:**
- Create: `core/xray.py`
- Create: `tests/test_xray.py`

**Interfaces:**
- Consumes: `DatabaseManager` from `core/database.py`
- Produces in `core/xray.py`:
  - `parse_xray_input(text: str) -> Optional[ dict ]`:
    Returns `None` if `text` is a command (`/start`, etc.) or plain chat message without a link, phone number, or apartment listing pattern. Otherwise returns:
    - `{"kind": "url", "source": "myhome" | "ss_ge", "source_id": str, "listing_id": str, "url": str, "phone_9": Optional[str]}`
    - `{"kind": "phone", "phone_9": str, "formatted_phone": str}`
    - `{"kind": "fb_text", "phone_9": Optional[str], "formatted_phone": Optional[str], "area_m2": Optional[float], "floor": Optional[str], "rooms": Optional[int], "price_usd": Optional[float], "district": Optional[str]}`
  - `evaluate_broker_verdict(target: Optional[dict], related_rows: list) -> dict`:
    Returns `{"is_broker": bool, "badge": str, "reason": str, "unique_apartments": int}`:
    - Flags as broker (`is_broker=True`, `badge="🕵️‍♂️ სავარაუდო ფარული მაკლერი / სააგენტო"`) if `target.is_owner == 0`, `user_type in ("agent", "agency", "broker", "developer")`, `author_listings_count >= 3`, OR `unique_apartments >= 2` (distinct area/district combinations under the same phone/author).
    - Flags as verified owner (`is_broker=False`, `badge="✅ რეალური მესაკუთრე (Owner)"`) if `is_owner == 1` and `unique_apartments <= 1` and `(author_listings_count or 1) <= 2`.
  - `find_exact_duplicates(conn, target: dict) -> list[dict]`:
    Queries `properties` by `group_id` (if set) and by `district = ? AND ABS(area_m2 - ?) <= 1.5 AND id != ?`, then filters in code so candidates with non-empty `floor` or `rooms` must match `target["floor"]` / `target["rooms"]`. Returns sorted by `price_usd ASC`.
  - `run_xray_on_db(db: DatabaseManager, text: str, live_details: Optional[dict] = None) -> Optional[str]`:
    Executes the full X-Ray report against `db` and returns the formatted Georgian HTML report string.

- [x] **Step 1: Write the failing tests in `tests/test_xray.py`**

Create `tests/test_xray.py` testing:
1. `test_parse_xray_input_variants`: MyHome URL, SS.ge URL, Georgian phone (`599 12 34 56`, `+995599123456`), Facebook post text (`"იყიდება საბურთალოზე 60 კვ.მ ბინა, მე-7 სართული, 3 ოთახი, ფასი 85000$, ტელ 599112233"`), and non-X-Ray inputs (`"/filter"`, `"45000 70000"`, `"გამარჯობა"` -> `None`).
2. `test_hidden_broker_verdict_from_phone_history`: A seller marked `user_type="physical"` on one listing, but whose phone number (`+995 599 11 22 33`) has 3 different apartments in `properties` -> flagged as `🕵️‍♂️ სავარაუდო ფარული მაკლერი / სააგენტო`.
3. `test_exact_duplicate_vs_different_floor_rejected`: Target is `60 m²` in `საბურთალო` on `floor="7"`. DB has two other `60 m²` listings in `საბურთალო`: one on `floor="7"` at `$79,000` (owner) and one on `floor="3"` at `$75,000`. X-Ray matches ONLY the `floor="7"` listing at `$79,000` (`-$6,000` cheaper) and excludes the `floor="3"` listing.
4. `test_initial_price_history_and_district_market_benchmark`: Target was initially `$95,000`, dropped to `$88,000`. X-Ray report displays `პირვანდელი ფასი: <b>$95,000</b>` and `დაკლებულია: <b>-$7,000</b>` plus district average `$/m²`.
5. `test_facebook_text_xray_finds_matching_portal_listing`: Pasting a Facebook text without a link finds the matching owner listing in D1 by district + area + floor and exposes whether the FB poster's phone belongs to a broker.

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_xray.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'core.xray'`)

- [x] **Step 3: Implement `core/xray.py`**

Create [`core/xray.py`](file:///home/user/Desktop/real-estate/core/xray.py) implementing `parse_xray_input`, `evaluate_broker_verdict`, `find_exact_duplicates`, `get_district_avg_m2`, and `run_xray_on_db` using indexed queries (`WHERE phone_number IN (?, ?)`, `WHERE author_id = ?`, `WHERE group_id = ?`, `WHERE district = ? AND area_m2 BETWEEN ? AND ?`).

- [x] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests/test_xray.py -v`
Expected: PASS (all 5 tests pass)

- [x] **Step 5: Commit**

```bash
git add core/xray.py tests/test_xray.py
git commit -m "feat(xray): implement Python X-Ray analyzer for links, phones, and FB text"
```

---

### Task 4: Integrate 24/7 On-Demand X-Ray into Cloudflare Worker (`cloudflare/worker.js`)

**Files:**
- Modify: `cloudflare/worker.js:430-460`
- Modify: `cloudflare/worker.js:630-645`
- Modify: `tests/test_xray.py`

**Interfaces:**
- Consumes: `env.DB` (Cloudflare D1 binding in `cloudflare/worker.js`)
- Produces in `cloudflare/worker.js`:
  - `handleTelegramWebhook` passes `env` to `handleMessage(update.message, token, kv, env)`.
  - Before the unrecognized-text fallback at line 632 of `handleMessage` (after `awaiting_price`, `awaiting_area`, and all `/command` handlers), calls `const xrayReply = await handleXRayQuery(text, env);` and if non-null, sends the HTML X-Ray report to `chatId`.
  - Self-contained Worker helpers in `cloudflare/worker.js`:
    - `parseXRayInput(text)`
    - `fetchLiveStatementDetails(source, sourceId, url)` (fetches MyHome/SS.ge detail page if listing is missing or unenriched in D1)
    - `handleXRayQuery(text, env)` (runs indexed D1 queries on `env.DB`, merges live details, evaluates broker verdict, finds exact duplicates & initial price delta, and formats the HTML response).

- [x] **Step 1: Write the failing Worker verification test in `tests/test_xray.py`**

Add `test_cloudflare_worker_xray_integration` in `tests/test_xray.py` that reads `cloudflare/worker.js` and verifies:
1. `handleMessage(update.message, token, kv, env)` is called with `env` in `handleTelegramWebhook`.
2. `awaiting_price` and `awaiting_area` state blocks appear BEFORE `handleXRayQuery(text, env)` inside `handleMessage`.
3. `cloudflare/worker.js` defines `parseXRayInput`, `fetchLiveStatementDetails`, and `handleXRayQuery`.
4. `cloudflare/worker.js` uses indexed `phone_number IN (?, ?)` and does NOT contain any unindexed `phone_number LIKE '%` query.
5. Balanced braces/parentheses/brackets check on `cloudflare/worker.js`.

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests/test_xray.py -v`
Expected: FAIL (`handleXRayQuery` not found in `cloudflare/worker.js`)

- [x] **Step 3: Implement X-Ray functions in `cloudflare/worker.js`**

1. Update `handleTelegramWebhook` in [`cloudflare/worker.js`](file:///home/user/Desktop/real-estate/cloudflare/worker.js#L436-L438) to pass `env` to `handleMessage(update.message, token, kv, env)`.
2. Update `async function handleMessage(msg, token, kv, env)` in [`cloudflare/worker.js`](file:///home/user/Desktop/real-estate/cloudflare/worker.js#L451-L638) so right before the fallback message at line 632, it calls `handleXRayQuery(text, env)`.
3. Implement `parseXRayInput(text)`, `fetchLiveStatementDetails(source, sourceId, rawUrl)`, and `handleXRayQuery(text, env)` at the bottom of `cloudflare/worker.js`.
4. Update the `/start` welcome message in `cloudflare/worker.js` with a 1-line hint: `🔍 <b>რენტგენი (X-Ray):</b> ჩააგდეთ ჩატში ბინის ლინკი, ტელეფონის ნომერი ან FB პოსტის ტექსტი გადასამოწმებლად.`

- [x] **Step 4: Run full test suite to verify everything passes**

Run: `python3 -m unittest tests/test_xray.py tests/test_batch_db.py tests/test_scrapers.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add cloudflare/worker.js tests/test_xray.py
git commit -m "feat(worker): add 24/7 on-demand X-Ray for links, phones, and FB posts in Telegram bot"
```
