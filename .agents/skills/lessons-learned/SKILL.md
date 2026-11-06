---
name: lessons-learned
description: >
  Facts about this system that the code does not make obvious and that have
  already caused a wrong conclusion or an outage. Use before investigating a
  failed or silent cron run, Cloudflare Worker or D1 quota errors, duplicate or
  missing Telegram alerts, a listing that slipped through a filter, a wrong
  owner/agent label, or before claiming a fix is verified.
---

# Lessons learned

Each entry is here because it cost something: an outage, a wrong diagnosis, or a stale claim someone repeated. This is not a style guide and it does not replace the project rules. It lists traps that repeat.

Every lesson names its evidence. Incidents come from [`docs/production-fixes.md`](../../../docs/production-fixes.md) and [`docs/adr/0001-eliminate-d1-full-table-scans.md`](../../../docs/adr/0001-eliminate-d1-full-table-scans.md). Code-behaviour entries say so and point at the file. If a lesson hardens into a rule, move it to the relevant skill or an ADR and leave a pointer here.

## Database and quotas

### Never fall back to an empty local database when D1 is configured

The cron runner has a fresh, empty disk on every run. When D1 returned an error, an earlier version fell back to local SQLite. `seen_ids.txt` and `user_notifications` were then empty, every listing looked new, and the bot re-alerted on listings it had sent days before.

The code now raises `DatabaseUnavailableError` and `main.py` exits with status 1 (`sys.exit(1)` on the halted-cycle path). A red run is the intended outcome.

**Do:** treat a failing run with a D1 or Worker error as a quota or outage problem to fix at the source. Do not add a local fallback "so the run goes green". Evidence: `docs/production-fixes.md` section 4; `core/database.py` (`DatabaseEngine.__init__`), `main.py`.

### Count Worker requests across every runner, not per run

The Worker proxies scraping, the Telegram webhook, KV, and D1. A free-tier daily request cap applies to all of that combined. The incident was two engines running the same cycle: the GitHub Actions cron and the `app.py` daemon loop. A per-listing HTTP call inside a loop (`save_listing`, `is_user_notified`, `_get_hashes`) turned about 500 to 700 requests per cycle into hundreds of thousands per day, from two subscribers.

**Do:** before claiming headroom, multiply requests per cycle by cycles per day across every place the cycle runs. Prefer the batch methods (`save_listings_batch`, `filter_unnotified_listings`, `_get_hashes_batch`). Evidence: `docs/production-fixes.md` section 2.

### D1 rejects more than 100 bound parameters, so chunk every `IN (...)`

Batch lookups are chunked at 50 so that one extra parameter plus 50 ids stays under the limit (`CHUNK_SIZE = 50` in `core/database.py` and `core/dedup.py`). A new `IN (...)` query with an unbounded list works on SQLite and fails on D1, and local tests use SQLite.

**Do:** chunk every new set-based query. Test the chunking with more than one chunk of ids (`tests/test_batch_db.py` has the pattern).

### Cold-start bulk loads exhaust D1 row reads

Every cron run starts cold. Loading whole tables into memory at start-up cost about 30,000 row reads per run. Together with the per-listing queries that pushed daily reads past the free-tier limit. After that, runs crashed within seconds and alerts stopped. ADR 0001 replaced the bulk load with per-row indexed lookups.

**Do:** when a run dies almost immediately with D1 errors, check row-read volume before suspecting the scrapers. Never add a start-up `SELECT` over a whole table. Evidence: `docs/adr/0001-eliminate-d1-full-table-scans.md`.

## Alerts

### "Notified" does not always mean Telegram accepted the message

`main.py` calls `mark_user_notified` and counts the alert when `TelegramNotifier.send_notification` returns `True`. That method also returns `True` when no bot or no recipient is configured, after printing to the console.

**Do:** when a cycle reports alerts sent and nobody received one, check that `TELEGRAM_BOT_TOKEN` is set in the environment that ran it, before looking at filters. A run without the token marks listings as notified, and they will not be sent again once the token is fixed. Evidence: `notifier/telegram_bot.py` (`send_notification`), `main.py`.

### A lower-price banner needs a strictly lower price

An apartment at `$68,000` was announced as a new lowest price while the other copies in its duplicate cluster were also `$68,000`. The banner now appears only when the current price is strictly below every other price in the cluster.

**Do:** when touching duplicate or price-drop text, test the equal-price case. Evidence: `docs/production-fixes.md` section 5.3; `tests/test_fixes.py` (`test_duplicate_strict_lowest_price_logic`).

## Classification

### `user_type: "physical"` is not proof of an owner

Private brokers register on MyHome as physical persons. They were labelled owners until `is_owner` false, agency-type values, and agent phrases in the description were given precedence over the physical type. For an owner-only subscriber, a wrong "owner" label is a wrong alert.

**Do:** keep the order in `ListingFilter.matches_hygiene`: explicit non-owner and agent signals first, owner signals after. Evidence: `docs/production-fixes.md` section 5.4; `tests/test_fixes.py` (`test_private_broker_physical_user_type_is_owner_zero`), `tests/test_owner_agent_filtering.py`.

### Keep the portal's own price badge separate from the local one

MyHome publishes its own price badge in the page's `__NEXT_DATA__` state. A median computed from stored listings once overwrote it, so an alert showed "high price" where the site said "low price". `valuation_scale_source` now records `"myhome"` or `"market"`, and the alert labels them differently.

**Do:** never overwrite a value the portal supplies with a value the bot computed. Evidence: `docs/production-fixes.md` section 5.1; `core/analytics.py`, `tests/test_fixes.py` (`test_myhome_valuation_scale_labeling`).

## Filters

### There are two matching paths and two sets of defaults

`ListingFilter.matches` applies the static search in `config/filters.json`. `ListingFilter.matches_user` applies each subscriber's saved filter, which lives in Cloudflare KV. Price, area, and room rules exist in both. A price leak of `$88k`–`$98k` listings came through the per-user path while the static path was correct. The fix was a two-stage check: a tentative pre-check before detail enrichment, then a strict check after.

`SearchFilters()` in `core/models.py` has different defaults from `config/filters.json` (sale band, area band, `rooms_min`). `tests/test_filters_validation.py` builds `SearchFilters()` directly, so editing `config/filters.json` changes production behaviour and changes none of those tests.

**Do:** when a filter rule changes, change both paths and say which defaults the test uses. When the KV subscriber call fails, the cached subscribers on disk (`core/user_sync.py`) stand in, so a stale cache can also explain a wrong alert. Evidence: `docs/production-fixes.md` section 5.2; `core/filters.py`, `core/models.py`, `config/filters.json`.

## X-Ray duplicate matching

### A missing field is not a matching field

The X-Ray matched a one-bedroom Temqa flat to a 4-room new build because an unknown room count matched any room count and a matching floor ended the check. The less the parser understood, the more listings matched. Most Facebook posts never say "ოთახი" (28 of the 100 golden posts do), so this was the normal case, not an edge case.

**Do:** compare each field as agree, conflict, or unknown. Let unknown add nothing, let a known conflict reject, and never let one weak field (floor, district) decide alone. Measure parser changes with the golden set in `tests/fixtures/xray_fb_posts.json`. Evidence: `docs/production-fixes.md` section 5.5; `cloudflare/worker.js` (`workerCandidateMatchesTarget`).

### The Python twin cannot catch JavaScript bugs

`core/xray.py` mirrors the Worker's X-Ray and has the Python tests. Production runs `cloudflare/worker.js`, where `Boolean({})` is true. An empty TNET `broker` object made a verified owner an agency, and the Python tests stayed green because `bool({})` is false. Test assertions that only check that a string exists in `worker.js` prove nothing about behaviour.

**Do:** test Worker behaviour in `tests/js/` (the real file in `node:vm`), and change the twin in the same commit. Evidence: `docs/production-fixes.md` section 5.5.

### A masked value is a value

A stored masked phone (`555000***` shape) counted as "has a phone", so the full number from the portal never replaced it and the duplicate was never re-fetched. A merge that only lets one direction win (a live `0` replaced an owner, a live `1` never replaced a `0`) keeps one bad write forever.

**Do:** when merging fresh portal data into stored rows, rank values by quality (full over masked over none) and let the fresh portal answer win both ways. Evidence: `cloudflare/worker.js` (`mergeLiveIntoRow`, `enrichDuplicatesIfNeeded`).

## Documentation

### Numbers in prose rot faster than the code

The old `README.md` said every ten minutes, 99 runs a day, and 33 tests. `.github/workflows/scrape.yml` runs every 15 minutes with a different night schedule, and the test suite was several times larger. `docs/production-fixes.md` calls the workflow `scraper.yml`, which is not its file name.

**Do:** read `scrape.yml` and the `tests/` directory before quoting a schedule or a test count. Prefer linking to the file over copying its numbers. Evidence: this repository at the time `README.md` was rewritten.
