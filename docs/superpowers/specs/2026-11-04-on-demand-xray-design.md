# On-Demand Listing & Phone X-Ray ("რენტგენი" ბოტში) — Design Spec

**Date:** 2026-11-04
**Status:** Approved

## 1. Problem Statement
When a user sees an apartment in a Facebook group, from a broker, or on MyHome.ge / SS.ge, they want to paste a **listing link**, a **phone number**, or **raw Facebook post text** directly into the Telegram bot and immediately receive an X-Ray report answering:
1. **Who is behind it?** Is the author a real owner (`✅ რეალური მესაკუთრე`) or a hidden broker/agency (`🕵️‍♂️ ფარული მაკლერი / სააგენტო`) based on account metadata, statement count, and other apartments tied to the same phone number or author ID?
2. **Is the exact same apartment listed cheaper elsewhere?** Using duplicate clusters (`group_id`) and strict multi-attribute matching (`district` + `area_m2 ±1.5` + `floor` + `rooms`), is there a cheaper or direct-owner listing on MyHome.ge or SS.ge?
3. **What was its initial price?** Did the price drop or increase compared to when the apartment was first scraped (`initial_price_usd` vs `price_usd`), and how does its `$/m²` compare to the district average in D1?

## 2. Design Improvements Over Naive Approach
1. **Rich Author Metadata on All Scraped Listings (`author_id`, `author_listings_count`):**
   - Because `fetch_statement_details` only runs on filter-matched listings in `main.py`, ~90% of rows in `properties` come from `save_listings_batch` (`unmatched_to_save`).
   - We add `author_id TEXT` and `author_listings_count INTEGER` to `PropertyListing` and `properties`, populated during both search page normalization (`_normalize_item`) and detail page enrichment (`fetch_statement_details`).
   - When a link is sent to the bot in Telegram, `cloudflare/worker.js` queries both D1 (`properties`) AND fetches live page data (`__NEXT_DATA__` / TNET statement API) to get live phone, author ID, and active statement count even for listings not yet enriched in D1.
2. **Zero-Full-Table-Scan Phone Lookup (`idx_properties_phone`):**
   - Instead of `WHERE phone_number LIKE '%599123456%'` (which forces a full-table scan in SQLite/D1), we index `phone_number` (`CREATE INDEX IF NOT EXISTS idx_properties_phone ON properties(phone_number)`) and `author_id` (`CREATE INDEX IF NOT EXISTS idx_properties_author_id ON properties(author_id)`).
   - Phone lookup normalizes input to 9 digits (`5XXXXXXXX`) and queries `WHERE phone_number IN (?, ?)` with `['+995 5XX XX XX XX', '5XXXXXXXX']`, reading only matching indexed rows.
3. **Two-Tier Duplicate & Market Comparison:**
   - **Exact Duplicate Match (`🎯 იგივე ბინა სხვაგან`):** Matches `group_id` OR compatible `district` (expanding subdistrict aliases such as `ნუცუბიძე -> საბურთალო`, `ვარკეთილი -> სამგორი`, `თემქა -> ნაძალადევი`, `წერეთელი -> დიდუბე`) + `ABS(area_m2 - ?) <= 1.5` + SQL-pushed `deal_type`, `rooms`, and `floor` constraints so cheap rent rows or other floors never exhaust `LIMIT 25`. When an FB post omits floor, requires matching positive `rooms` and `price_usd` within `±10%`. Top matched duplicates missing phone or owner status are live-enriched via `fetchLiveStatementDetails`, and each duplicate row displays its owner/agency/unverified badge and phone number (`📞`).
   - **Phoneless Facebook Post Recovery:** When an FB post contains no phone number, X-Ray derives the seller verdict and header phone number from the matched portal duplicate (`duplicates[0]`) after live enrichment; if no portal duplicate exists yet, it explicitly reports that no phone was provided and no matching apartment was found in D1.
   - **District Market Benchmark (`📊 უბნის საშუალო ფასი`):** Computes average `price_per_m2` in the same `district` (with subdistrict alias fallback) and shows percentage difference (`-12%` / `+15%`).
4. **Initial Price Tracking (`initial_price_usd`):**
   - Add `initial_price_usd REAL` to `properties`.
   - On first `INSERT`, `initial_price_usd` equals `price_usd`.
   - On `ON CONFLICT(id) DO UPDATE`, `initial_price_usd = COALESCE(properties.initial_price_usd, properties.price_usd, excluded.price_usd)` so the original price is permanently preserved while `price_usd` updates on any price change.
5. **Triple Input Parser (Link + Phone + Facebook Post Text):**
   - Recognizes MyHome.ge URLs (`myhome.ge/.../pr/<id>`), SS.ge URLs (`home.ss.ge/...-<id>` or `ss.ge/.../<id>`), Georgian mobile numbers (`5XX XX XX XX`), and multi-line Facebook post descriptions containing area (`მ²`/`კვ`), floor (`სართ`), price (`$`),district, and/or phone number.

## 3. Architecture & Constraints
- **`cloudflare/worker.js` Single-File Deployment:** Per `cloudflare/README.md`, `cloudflare/worker.js` is deployed as a single self-contained file in Cloudflare Dashboard. All Worker X-Ray functions must live inside `cloudflare/worker.js` with no external JS imports.
- **`core/xray.py` Python Reference & Verification Engine:** Provides identical input classification, broker verdict evaluation, duplicate matching, and HTML message formatting in Python so the full X-Ray logic is covered by automated unit tests (`tests/test_xray.py`).
- **D1 Quota Safety:** Every D1 query in X-Ray uses primary key `id`, `idx_properties_phone`, `idx_properties_author_id`, `idx_properties_group_id`, or `idx_properties_district_area` with `LIMIT 20`.
