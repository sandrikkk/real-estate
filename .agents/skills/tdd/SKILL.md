---
name: tdd
description: Test-driven development for the Real Estate Scraper & Alerting Platform — write the failing test FIRST whenever the intended behaviour is well-specified (scraper parsing, location matching, owner/agent filtering, market analytics, user deduplication, bug fixes). Use BEFORE writing implementation code, or when asked "which tests do I run for this change", "add a failing test", or "fix this bug".
---

# Test-Driven Development (TDD) — Real Estate Platform

This project enforces a **strict, test-first** discipline for deterministic components: scraper parsing, district matching, owner/agent classification, database deduplication, and bug fixes.

Before writing implementation code:
1. Define the success criteria as an executable test case.
2. Run it and confirm it **FAILS** for the expected reason (**Red**).
3. Implement the minimum code necessary to make it pass (**Green**).
4. Refactor and ensure all tests in the repository remain green (**Refactor**).

---

## Test Selection by Change Surface

Consult this matrix BEFORE writing code. The column on the right is the mandatory test command that MUST be green before committing:

| Change Touches… | Test Type | File to Update / Add | Required Test Run |
|---|---|---|---|
| **Scraper Parsing & API URL Generation** (`scrapers/myhome.py`, `ss_ge.py`, `area_ge.py`) | Unit / API contract | `tests/test_scrapers.py` | `.\.venv\Scripts\python.exe -m unittest tests/test_scrapers.py` |
| **Location & District Synonyms** (`core/filters.py`, `DISTRICT_SYNONYMS`, subdistricts) | Unit / Location | `tests/test_location_filtering.py` | `.\.venv\Scripts\python.exe -m unittest tests/test_location_filtering.py` |
| **Owner vs. Agent Classification** (`is_owner`, physical vs agency, detail enrichment) | Unit / Classification | `tests/test_owner_agent_filtering.py` | `.\.venv\Scripts\python.exe -m unittest tests/test_owner_agent_filtering.py` |
| **Hygiene, Stop-Words & Search Filters** (`ListingFilter`, black frame, stop words, price/area ceilings) | Unit / Filter logic | `tests/test_filters.py` | `.\.venv\Scripts\python.exe -m unittest tests/test_filters.py` |
| **Rental Flow & Deal Types** (rent prices, deal type validation, rent vs sale envelopes) | Unit / Deal type | `tests/test_rental_flow.py` | `.\.venv\Scripts\python.exe -m unittest tests/test_rental_flow.py` |
| **Market Analytics & Valuation Scale** (`MarketAnalytics`, IQR calculation, hot deal badges) | Unit / Valuation | `tests/test_analytics.py` | `.\.venv\Scripts\python.exe -m unittest tests/test_analytics.py` |
| **Database Engine & Persistence** (`DatabaseEngine`, SQLite operations, `seen_ids`) | Unit / Database | `tests/test_core.py` | `.\.venv\Scripts\python.exe -m unittest tests/test_core.py` |
| **Multi-User Deduplication & Sync** (per-user notification isolation, subscriber matching) | Unit / Multi-user | `tests/test_user_dedup.py` | `.\.venv\Scripts\python.exe -m unittest tests/test_user_dedup.py` |
| **Full Pipeline Integration** (Scrape -> Filter -> Enrich -> Analyze -> Notify) | Integration / Flow | `tests/test_end_to_end_flow.py` | `.\.venv\Scripts\python.exe -m unittest tests/test_end_to_end_flow.py` |
| **Bug Fix Anywhere** | Reproduction test | Same file as the affected surface | The test case you wrote |
| **Pre-Flight Before Any Commit or Deploy** | Full Repository Suite | All test suites in `tests/` | `.\.venv\Scripts\python.exe -m unittest discover tests` |

---

## When to Write the Test FIRST

Always write the test before writing code when:
- **Reproducing a Bug:** Never fix a bug without writing a test reproducing the exact failure first. The test proves the bug exists and guarantees it never regresses.
- **Adding a Scraper Normalizer or Field:** E.g. parsing a new field (`video_link`, `condition_id`, phone unmasking) — add the raw payload to `tests/test_scrapers.py` and assert the normalized listing property.
- **Adding District Synonyms or Mappings:** E.g. adding a new micro-neighborhood — write the test in `tests/test_location_filtering.py` first.
- **Modifying Owner/Agent Logic:** E.g. adding a new keyword or API property identifying brokers — write a test in `tests/test_owner_agent_filtering.py`.
- **Modifying Analytics or Bargain Rules:** E.g. adjusting IQR discount calculation — write the test in `tests/test_analytics.py`.

---

## The TDD Loop in Practice

### Step 1: Red (Write the Failing Test)
Create the smallest possible test case asserting the expected behavior:

```python
def test_ss_ge_extracts_rooms_from_title_when_missing(self):
    scraper = SSGeScraper()
    raw = {
        "id": 12345,
        "title": "იყიდება 3-ოთახიანი ბინა საბურთალოზე",
        "room": None,
        "price": {"2": {"price_total": 90000}},
        "area": 75.0,
    }
    listing = scraper._normalize_item(raw)
    self.assertIsNotNone(listing)
    self.assertEqual(listing.rooms, 3)
```

Run the test:
```powershell
.\.venv\Scripts\python.exe -m unittest tests/test_scrapers.py -k test_ss_ge_extracts_rooms_from_title_when_missing
```
Confirm it **fails** (e.g. `AssertionError: None != 3`).

### Step 2: Green (Write the Minimum Code)
Implement only what is needed to make the test pass:
```python
if not rooms and title:
    room_match = re.search(r"(\d+)\s*(?:-|–)?\s*ოთახ", title)
    if room_match:
        rooms = int(room_match.group(1))
```

Re-run the test:
```powershell
.\.venv\Scripts\python.exe -m unittest tests/test_scrapers.py -k test_ss_ge_extracts_rooms_from_title_when_missing
```
Confirm it **passes**.

### Step 3: Refactor & Full Verification
Clean up code, ensure readability, and run the entire suite to verify zero regressions:
```powershell
.\.venv\Scripts\python.exe -m unittest discover tests
```
All **71+ tests must be green (`OK`)**.

---

## Mocking & Network Discipline

1. **Deterministic Unit Tests:** Unit tests in `tests/test_scrapers.py`, `tests/test_filters.py`, `tests/test_location_filtering.py`, etc., must be completely offline, deterministic, and fast (&lt; 1 second total execution). Use static dictionaries representing real API/HTML payloads.
2. **Never Make Uncontrolled Network Calls in CI Tests:** Do not hit production websites (`ss.ge`, `myhome.ge`) in automated test suites; portal rate-limits or temporary network blips will cause flaky test failures.
3. **End-to-End Simulation:** In `tests/test_end_to_end_flow.py`, mock external network and Telegram endpoints (`send_listing_alert`), but run real `DatabaseEngine`, `ListingFilter`, and `MarketAnalytics` logic.

---

## When to SKIP TDD

You may skip test-first only for:
- One-line documentation fixes or formatting changes in Markdown files.
- Minor logging string adjustments.
- Adjusting polling intervals or sleep delays.
*(Even when skipping TDD, always run `.\.venv\Scripts\python.exe -m unittest discover tests` before pushing changes).*
