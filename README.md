---
title: Tbilisi Real Estate Radar 24/7
emoji: 🏢
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 4.44.1
app_file: app.py
pinned: false
---

# Real Estate Market Tracker & NTFY Notifier 🏢

Autonomous asynchronous Python system designed to scrape, aggregate, deduplicate, filter, and alert on real estate listings from major Georgian portals (**MyHome.ge**, **SS.ge**, **Area.ge**) via **NTFY.sh** push notifications.

---

## 📱 რატომ NTFY?

- **არანაირი ბოტის შექმნა არ გჭირდებათ!**
- მუშაობს iOS, Android და Web ბრაუზერებზე.
- აგზავნის ფოტოებს, ფასს, ფართობს, მისამართს, სარფიანობის შეფასებას და პირდაპირ ღილაკს ბინაზე გადასასვლელად.

---

## 🏗 Directory & File Structure

```text
real_estate_tracker/
├── config/
│   ├── __init__.py
│   ├── settings.py              # NTFY configurations & runtime constants
│   └── filters.json             # Search criteria & parameter thresholds
├── core/
│   ├── __init__.py
│   ├── models.py                # Pydantic data schemas & DistrictPriceStats
│   ├── database.py              # SQLite storage & deduplication engine
│   ├── analytics.py             # Price/m² analytics & bargain/outlier detector
│   └── filters.py               # Regex stop-words & multi-parameter filter
├── scrapers/
│   ├── __init__.py
│   ├── base.py                  # Abstract base scraper interface (curl_cffi)
│   ├── myhome.py                # MyHome.ge Next.js __NEXT_DATA__ extractor
│   ├── ss_ge.py                 # SS.ge Next.js __NEXT_DATA__ extractor
│   └── area_ge.py               # Area.ge scraper with resilient timeout handling
├── notifier/
│   ├── __init__.py
│   ├── ntfy_notifier.py         # Asynchronous NTFY push notification engine
│   └── telegram_bot.py          # Telegram bot engine (optional secondary fallback)
├── data/
│   └── properties.db            # SQLite database file
├── main.py                      # Async orchestrator & scheduling loop
├── requirements.txt
└── README.md
```

---

## 🚀 სწრაფი დაყენება და გაშვება (1 წუთში)

### 1. NTFY აპლიკაცია თქვენს ტელეფონზე
1. გადმოწერეთ **ntfy** აპლიკაცია თქვენს მობილურში ([App Store (iOS)](https://apps.apple.com/app/ntfy/id1625396347) ან [Google Play (Android)](https://play.google.com/store/apps/details?id=io.heckel.ntfy)) ან გახსენით ბრაუზერში: [ntfy.sh](https://ntfy.sh).
2. აპლიკაციაში დააჭირეთ **`+`** (Subscribe to topic) და ჩაწერეთ თქვენთვის სასურველი უნიკალური სახელი, მაგალითად: `tbilisi_flats_9911`

### 2. კონფიგურაცია (`.env`)
პროექტის საქაღალდეში შექმენით `.env` ფაილი და მიუთითეთ თქვენი Topic-ის სახელი:

```env
NTFY_TOPIC=tbilisi_flats_9911
NTFY_SERVER_URL=https://ntfy.sh
CHECK_INTERVAL_SECONDS=180
BARGAIN_DISCOUNT_THRESHOLD_PCT=15.0
```

### 3. ფილტრების მორგება
[`config/filters.json`](file:///c:/Users/user/PycharmProjects/PythonProject1/config/filters.json) ფაილში მიუთითეთ სასურველი ფასის ზღვრები, ფართობი და უბნები.

### 4. გაშვება
```bash
python main.py
```

ახალი ბინის გამოქვეყნებისთანავე თქვენს ტელეფონზე მომენტალურად მოვა Push შეტყობინება ფოტოთი, დეტალებით და ბმულით! 🔥
