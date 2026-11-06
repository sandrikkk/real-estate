# Tbilisi Real Estate Scraper & Telegram Alert Bot

Open-source async Python pipeline that reads **MyHome.ge** and **SS.ge**, applies the rules in [`config/filters.json`](config/filters.json), and sends Telegram alerts. The scheduled job is [`.github/workflows/scrape.yml`](.github/workflows/scrape.yml) on a public GitHub repository. Seen listings and per-user subscriptions live on Cloudflare (D1 and KV), through the Worker in [`cloudflare/`](cloudflare/README.md).

`main.py` loads `config/filters.json`. The defaults on `SearchFilters` in `core/models.py` are a fallback (sale `$48,000`–`$72,000`, 48–62 m², `rooms_min` 2) and are what several unit tests construct directly. The numbers below are the file the scraper actually reads.

---

## Filters

Sale band in `config/filters.json`: **`$45,000`–`$75,000`**, **40–65 m²**, `rooms_min` 1. Rent band: **`$300`–`$1,500`** per month.

Still dropped in `core/filters.py` even when the JSON floor is 1 room:

- Studio layout: `bedrooms < 1` and `rooms < 2`.
- Under construction: `status_id == 3`, or Georgian wording for "under construction" / handover, including the years 2027, 2028, and 2029. The exact strings are in `config/filters.json`.
- Black frame: `condition_id == 6`, plus the Georgian phrase for black frame in the title or description.
- Green frame, white frame, and white-plus on a **sale**, when price is above `$54,000` (`white_frame_max_price`).
- Blacklisted streets and districts (keyword list in the JSON): Salosi and Khmelnitski, Lilo, Didi Dighomi, Mukhiani, Afrika, Dampalo, upper Ortachala, Varketili 3rd/4th plateaus and Varketili farming settlement, Orkhevi, Ponichala, airport settlement.
- Gldani micro-districts 3 through 8. Micro-districts 1 and 2 stay eligible.

Deal tags (thresholds in the same JSON):

- `🚨 HOT DEAL (RENOVATED)` — renovated, at or under `$1,350` / m².
- `🔥 VALUE FRAME (<$54k)` — frame at or under `$54,000` and at or under `$1,050` / m².

Owner vs agent comes from portal metadata plus description phrases.

**Metro IDs** in the JSON: 1, 7, 9, 10, 16, 17, 18, 19, 21, 22, 23, 24, 25. Names known to `scrapers/myhome.py`: State University, Station Square, Samgori, Isani, Nadzaladevi, Gotsiridze, Didube, Ghrmaghele, Sarajishvili, Akhmeteli Theatre, Varketili. IDs 24 and 25 are in the config and are not named in that map.

If a listing has no metro id, it has to match the district whitelist: Didube, Nadzaladevi, Gldani, Vake, Saburtalo, Dighomi massiv. Isani and Samgori are reached through metro ids 10 and 9.

Each active subscriber can narrow price, area, districts, deal type (sale / rent / both), and owner vs agent. Those profiles sit in Cloudflare KV and are applied in `matches_user`.

---

## Alerts

`notifier/telegram_bot.py` builds an HTML message: price, condition, district, street, metro, owner or agent, floor, rooms, listing link, and a `tel:` link when the number is complete. Labels in the live chat are Georgian. The same layout in English:

A later scrape of the same listing at a lower price adds a price-drop block. Photo duplicates (dHash in `core/dedup.py`) are grouped, and a new lowest price in the group is called out.

The Worker also answers an on-demand X-Ray check: paste a MyHome or SS.ge link, a Georgian phone number (full or masked `5XXXXX***`), or Facebook post text (even without a phone number) in the bot to uncover the seller verdict (owner vs hidden broker), recover the portal phone number, check initial price history (`initial_price_usd`), and find cheaper or direct-owner duplicates of the same apartment. Setup, filter buttons (`/filter`, `/owner`), and X-Ray use cases are in [`cloudflare/README.md`](cloudflare/README.md).

```text
HOT DEAL (RENOVATED)
$68,000 | 52.0 m² | $1,308/m²

Condition: Newly renovated
Location: Isani | Nadirashvili St. | Metro Isani
Owner | Floor: 4/9 | 2 rooms, 1 bedroom

Listing link (MyHome.ge)
+995 599 12 34 56 (one-tap call)
```

---

## Schedule

[`.github/workflows/scrape.yml`](.github/workflows/scrape.yml) runs `python main.py --once` on Python 3.11. Job timeout is 6 minutes. `workflow_dispatch` is enabled. The job checks out the repo with `contents: read` and does not commit a database.

| Window | Tbilisi (UTC+4) | Cron (UTC) | Runs / day |
| :--- | :--- | :--- | ---: |
| Day | 08:00–23:45 | `*/15 4-19 * * *` | 64 |
| Night | 00:00, 02:00, 04:00, 06:00 | `0 20,22,0,2 * * *` | 4 |
| Total |  |  | **68** |

Standard GitHub-hosted runners are free on public repositories, so this cron stays on the open-source Actions tier. The per-job timeout is 6 minutes.

---

## State

With `CLOUDFLARE_PROXY_URL` set, `DatabaseEngine` uses Cloudflare D1 through the Worker. The Actions workflow sets `USE_CLOUDFLARE_D1=True`. If that connection fails, the cycle raises `DatabaseUnavailableError` and stops, so an empty local file cannot replay old alerts.

With no proxy URL, the same engine uses SQLite at `data/properties.db`.

---

## Layout

```text
├── config/
│   ├── filters.json              # live search band, metro ids, whitelist, blacklist
│   └── settings.py
├── core/
│   ├── models.py                 # PropertyListing, SearchFilters (fallback defaults)
│   ├── filters.py                # hygiene, frame cap, deal tags, per-user match
│   ├── database.py               # D1 client and local SQLite
│   ├── dedup.py                  # photo dHash duplicate groups
│   ├── analytics.py
│   ├── user_sync.py              # active subscribers from the Worker
│   └── xray.py
├── scrapers/
│   ├── base.py                   # curl_cffi, Chrome 124 impersonation
│   ├── myhome.py                 # TNET statements API, then __NEXT_DATA__
│   ├── ss_ge.py
│   └── area_ge.py                # parser module; the cycle runs MyHome and SS.ge
├── notifier/
│   ├── telegram_bot.py
│   └── ntfy_notifier.py
├── cloudflare/
│   ├── worker.js                 # Telegram webhook, KV filters, D1 proxy, X-Ray
│   └── README.md
├── scripts/deploy_worker.py      # Worker deploy without committing wrangler.toml
├── .github/workflows/
│   ├── scrape.yml
│   └── deploy-worker.yml
├── app.py                        # optional Gradio status page around the same loop
├── main.py
└── tests/
```

MyHome calls `https://api-statements.tnet.ge/v1/statements` with `X-Website-Key: myhome`. TLS impersonation is `chrome124`.

---

## Local run

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
```

`.env` (see [`.env.example`](.env.example)):

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
CLOUDFLARE_PROXY_URL=
CLOUDFLARE_SYNC_KEY=
ENABLE_MULTI_USER=True
```

```bash
python -m unittest discover tests
node --test tests/js/*.test.mjs
python main.py --once
```

The tests cover frame caps, construction and black-frame rejection, blacklist and Gldani micro-districts, deal tags, owner vs agent, rent vs sale, per-user matching, D1 fail-closed behavior, price drops, Telegram formatting, and X-Ray.

---

## Deploy

Pushing to `master` is what the scraper cron checks out. The Worker workflow [`.github/workflows/deploy-worker.yml`](.github/workflows/deploy-worker.yml) deploys when `cloudflare/worker.js`, `scripts/deploy_worker.py`, or that workflow file changes. Local equivalent:

```bash
python scripts/deploy_worker.py
```

Bindings, webhook URL, and the GitHub secrets for that workflow are documented in [`cloudflare/README.md`](cloudflare/README.md). `wrangler.toml` is generated at deploy time and is not committed.

On Windows, `.agents/skills/deploy-action/scripts/deploy.ps1` runs the unit tests and pushes. It is the git helper. Worker deploys stay on `scripts/deploy_worker.py`.
