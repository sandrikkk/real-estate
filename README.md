# Tbilisi Real Estate Scraper & Telegram Alert Bot 🏢⚡

An autonomous, asynchronous Python system that continuously monitors leading Georgian real estate portals (**MyHome.ge**, **SS.ge**), filters listings against strict investment criteria, and delivers real-time, mobile-optimized alerts to **Telegram**.

The entire pipeline runs serverless 24/7 on **GitHub Actions** with zero hosting costs, automated database synchronization, and quota-optimized scheduling.

---

## 🌟 Key Features

1. **Direct TNET / MyHome REST API Integration**:
   - Queries `https://api-statements.tnet.ge/v1/statements` directly with `X-Website-Key: myhome` and modern TLS fingerprint impersonation (`curl_cffi` Chrome 124).
   - Completely bypasses Cloudflare bot protection and anti-scraping challenges while retrieving structured JSON payloads in under 2 seconds.
   - Secondary fallback to SSR HTML extraction via `__NEXT_DATA__` dehydrated state.

2. **Defensive Post-Processing & Filtering**:
   - **Price & Area Boundaries**: $48,000 – $72,000 USD | 48 – 62 m² | Minimum 2 rooms (1 bedroom + living room/studio; studio single-room layouts excluded).
   - **100% Under Construction Exclusion**: Immediate drop for status `მშენებარე` (`status_id == 3` or keywords such as „ჩაბარდება“, „2027“, „2028“, „2029“). Only completed / delivered buildings accepted.
   - **Black Frame (შავი კარკასი) Exclusion**: Dropped immediately (`condition_id == 6`).
   - **Frame Price Ceiling**: Green Frame (მწვანე კარკასი) and White Frame (თეთრი კარკასი) permitted **ONLY if price $\le \$54,000$** (leaving budget headroom for finishing works). Discarded if $> \$54,000$.
   - **Turnkey / Renovated**: Permitted up to $\le \$72,000$.

3. **Strict Location Safeguards**:
   - **Blacklisted Areas (Instant Drop)**:
     - Gabriel Salosi Ave & Bogdan Khmelnitski depths
     - Lilo (Didi Lilo, Patara Lilo)
     - Didi Dighomi, Mukhiani (depths/dachas)
     - Afrika, Dampalo, Ortachala upper hills
     - Upper Varketili plateaus (3rd/4th plateaus), Orkhevi, Ponichala, Airport settlement.
   - **Gldani Strict Boundary**: Permitted **ONLY in Micro-districts 1 and 2** (or adjacent to Akhmeteli/Sarajishvili metro). Micro-districts 3 through 8 are strictly rejected.
   - **Target Metro Stations**: Akhmeteli, Sarajishvili, Ghrmaghele, Didube, Gotsiridze, Nadzaladevi, Station Square, Isani, Samgori, Delisi, Vazha-Pshavela, State University.
   - **District Whitelist Fallback**: Didube, Nadzaladevi, Chugureti, Isani (metro proximity), Gldani (m/r 1-2 only).

4. **Deal Tagging & Seller Identification**:
   - `🚨 HOT DEAL (RENOVATED)`: Fully renovated apartment with price per sq.m $\le \$1,350/m²$.
   - `🔥 VALUE FRAME (<$54k)`: Green/White Frame priced $\le \$54,000$ with price per sq.m $\le \$1,050/m²$.
   - **Seller Verification**: Distinguishes `👤 მესაკუთრე (Owner)` vs `👤 სააგენტო (Agent)` via metadata and description inspection.

5. **Stateful Deduplication & GitHub Sync**:
   - SQLite store (`data/properties.db`) tracks processed listing IDs and fingerprint hashes.
   - GitHub Actions automatically commits the database post-execution (`[skip ci]`), ensuring zero duplicate alerts across cron intervals.

---

## ⏱ GitHub Actions Schedule & Quota Management

Configured in [`.github/workflows/scrape.yml`](file:///.github/workflows/scrape.yml) to maximize alert responsiveness while preserving a 10%+ safety margin under GitHub's 3,000 included minutes limit:

| Time Window | Tbilisi Time (GET / UTC+4) | UTC Schedule | Frequency | Daily Executions |
| :--- | :--- | :--- | :--- | :--- |
| **Active Daytime** | **08:00 – 00:00 (Midnight)** | `04:00 – 20:00 UTC` | **Every 10 minutes** (`*/10 4-19 * * *`) | 96 runs |
| **Night Hours** | **00:00 – 08:00** | `20:00 – 04:00 UTC` | **Every 2 hours** (`0 22,0,2 * * *`) | 3 runs (02:00, 04:00, 06:00 GET) |
| **Total Daily** | — | — | — | **99 runs / day** |

### Budget & Cost Safety
- **Monthly Usage**: $99 \text{ runs} \times 26 \text{ days} = 2,574 \text{ min} + 37 \text{ min used} = \mathbf{2,611\text{ minutes}}$ out of 3,000 (**87.0% quota**).
- **Safety Reserve**: **389 minutes (13.0%)** reserved for network fluctuations and runner delays.
- **Billed Amount**: **$0** (guaranteed free of charge).

---

## 📲 Telegram Alert Payload Sample

Formatted for fast mobile decision-making:

```text
🚨 HOT DEAL (RENOVATED)
💰 $68,000 | 52.0 m² | $1,307/m²

🛠 მდგომარეობა: ახალი გარემონტებული
📍 ლოკაცია: ისანი | ნადირაშვილის ქ. | 🚇 მ. ისანი
👤 მესაკუთრე (Owner) | 🏢 სართული: 4/9 | 🚪 2 ოთახი, 1 საძინებელი

🔗 განცხადების ლინკი (MyHome.ge)
📞 +995 599 12 34 56 (One-tap direct calling)
```

---

## 🏗 Repository Structure

```text
├── config/
│   ├── filters.json                 # Active search parameters, whitelist, and blacklists
│   └── settings.py                  # Environment config, Telegram token, and runtime constants
├── core/
│   ├── models.py                    # Pydantic schemas (PropertyListing, SearchFilters)
│   ├── filters.py                   # ListingFilter validation, frame price caps, and deal tags
│   ├── database.py                  # SQLite auto-migrating repository & deduplication
│   └── analytics.py                 # Outlier detection & neighborhood market baselines
├── scrapers/
│   ├── base.py                      # BaseScraper abstract class & TLS session provider
│   ├── myhome.py                    # TNET statements REST API client + SSR fallback
│   ├── ss_ge.py                     # SS.ge Next.js __NEXT_DATA__ extractor
│   └── area_ge.py                   # Area.ge scraper
├── notifier/
│   └── telegram_bot.py              # Mobile-first Telegram HTML alert generator
├── .agents/
│   └── skills/deploy-action/        # Automated deployment runbook & PowerShell helper
├── tests/                           # 33 comprehensive unit test suites (100% pass)
├── .github/workflows/scrape.yml     # 24/7 GitHub Actions cron orchestrator
└── main.py                          # Async orchestrator & cycle runner
```

---

## 🚀 Local Development & Testing

### 1. Installation
```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Environment Configuration (`.env`)
```env
TELEGRAM_BOT_TOKEN="your_bot_token_here"
TELEGRAM_CHAT_ID="your_chat_id_here"
```

### 3. Run Automated Tests
```bash
python -m unittest discover tests
```
Runs 33 unit tests validating:
- Frame price caps ($54,000 threshold)
- Under-construction & black-frame rejection
- Location blacklists (Salosi, Lilo, plateaus, etc.)
- Gldani 1-2 micro-district restriction
- Deal tags (`🚨 HOT DEAL`, `🔥 VALUE FRAME`)
- Telegram alert formatting & one-tap calling links

### 4. Execute a Single Live Scraping Cycle
```bash
python main.py --once
```

---

## 🚢 Automated Deployment (`deploy-action`)

To safely push changes and update the GitHub Actions workflow without database collisions:

```powershell
powershell -ExecutionPolicy Bypass -File .agents/skills/deploy-action/scripts/deploy.ps1 -CommitMessage "Your commit message"
```

The automated script:
1. Runs full local test suites (`unittest discover tests`).
2. Performs surgical git staging.
3. Automatically protects `data/properties.db` from remote conflict and rebases against `origin/master`.
4. Re-verifies tests on the rebased tree.
5. Pushes to `origin/master`, instantly updating the **Real Estate Scraper 24/7** workflow.
