# 0001 — Eliminate D1 Full-Table Scans on CI Cold Start

## Status
Accepted

## Context & Problem Statement

The Real Estate Scraper runs on GitHub Actions every 10 minutes (daytime) as stateless CI jobs. Each run is a **cold start** — fresh checkout, no local state files. The `DatabaseEngine.init_db()` was syncing the entire `properties` and `user_notifications` tables from Cloudflare D1 into in-memory caches on every startup:

```
SELECT id FROM properties                              → ~10K rows
SELECT chat_id, listing_id FROM user_notifications      → ~10K rows
SELECT id, phone_number FROM properties WHERE phone_number IS NOT NULL → ~10K rows
```

At ~100 CI runs/day × ~30K rows per init = **~3M row reads/day** just for initialization — consuming 60% of D1's free tier limit (5M/day). Combined with per-listing queries (dedup candidates, hash lookups, group info), the system was exceeding 5M daily and triggering **D1 row-read blocks**, which caused all subsequent CI runs to fail (28-second crashes) and stopped all notifications.

Additionally, `dedup.py._check_listing_inner()` was calling `_get_hashes()` 2–3 times per candidate (once for categorization into "with hash" list, once for "without hash" list, once inside `_ensure_hashes()`), an N+1 query pattern wasting D1 HTTP round-trips.

## Decision & Specification

**Affected components:** `core/database.py`, `core/dedup.py`

### 1. Skip full-table sync for D1 mode (`database.py:init_db()`)

When `self.use_d1 is True`, do **not** execute the three bulk SELECT queries at startup. The in-memory caches (`_seen_ids`, `_user_seen`) start empty in CI and are populated incrementally:

- `_seen_ids`: Populated via `_append_seen_id()` during `save_listing()`. The `is_seen()` method already has a per-row DB fallback (`SELECT 1 FROM properties WHERE id = ?`), so the empty cache is safe.
- `_user_seen`: A new **per-row DB fallback** was added to `is_user_notified()`:

```python
def is_user_notified(self, chat_id, listing_id):
    if listing_id in self._user_seen.get(str(chat_id), set()):
        return True
    if self.use_d1:
        # Indexed lookup: PRIMARY KEY (chat_id, listing_id) → 1 row max
        cursor.execute("SELECT 1 FROM user_notifications WHERE chat_id = ? AND listing_id = ?", ...)
        if cursor.fetchone(): ...
    return False
```

### 2. Skip phone index bulk load for D1 mode (`dedup.py:_load_phone_index()`)

The phone→listing_ids index is built incrementally during `check_listing()` via the existing `self._phone_index[listing.phone_number].add(listing.id)` at line 297.

Trade-off: The "probable owner" heuristic in `_get_group_info()` loses historical phone frequency data for the current cycle. This is acceptable — the heuristic is advisory, not a notification gate.

### 3. Cache dedup hash lookups (`dedup.py:_check_listing_inner()`)

```python
# Before: 2-3 DB calls per candidate
cands_with_hash = [c for c in candidates if self._get_hashes(c["id"])]      # call 1
cands_without_hash = [c for c in candidates if not self._get_hashes(c["id"])] # call 2
# ...
cand_hashes = await self._ensure_hashes(...)  # call 3 (_get_hashes inside)

# After: 1 DB call per candidate, cached
cand_hash_cache = {c["id"]: self._get_hashes(c["id"]) for c in candidates}
cands_with_hash = [c for c in candidates if cand_hash_cache[c["id"]]]
cands_without_hash = [c for c in candidates if not cand_hash_cache[c["id"]]]
# ...
cand_hashes = cand_hash_cache.get(cand["id"]) or await self._ensure_hashes(...)
```

## Consequences

- **Positive:** Daily D1 row reads drop from ~4–5M to ~200–500K (well within the 5M free tier). CI runs no longer crash from D1 blocks. Notifications resume normally after reset.
- **Trade-offs:** `new_count` stat in cycle summary is inflated on the first cycle (all listings appear "new" since `_seen_ids` is empty), but this is cosmetic — no functional impact. Phone index loses historical depth per cycle.
- **Safety:** `is_user_notified()` DB fallback uses the `PRIMARY KEY (chat_id, listing_id)` index, guaranteeing at most 1 row scanned per check. No re-notification risk. `INSERT OR IGNORE` in `save_listing()` prevents duplicates regardless of cache state.
