# 🚀 Cloudflare Worker 24/7 Telegram Control Panel Setup

ეს დოკუმენტი აღწერს, თუ როგორ უნდა დააყენოთ Cloudflare Worker-ი, რათა თქვენს Telegram ბოტს ჰქონდეს 24/7 სამართავი პანელი (ღილაკები) და მომხმარებლებმა თავად შეცვალონ თავიანთი ფილტრები.

---

## 1. Cloudflare Dashboard-ში KV Namespace-ის შექმნა

1. შედით [Cloudflare Dashboard](https://dash.cloudflare.com/)-ში.
2. მარცხენა მენიუში აირჩიეთ: **Workers & Pages** -> **KV**.
3. დააჭირეთ ლურჯ ღილაკს **Create a namespace**.
4. ჩაწერეთ სახელი: `USERS_KV` და დააჭირეთ **Add**.

---

## 2. Worker-ის კოდის განახლება

1. გახსენით თქვენი Worker-ი (ან შექმენით ახალი): **Workers & Pages** -> თქვენი Worker -> **Edit code**.
2. წაშალეთ იქ არსებული კოდი და ჩასვით ფაილ [cloudflare/worker.js](worker.js)-ის სრული შინაარსი.
3. დააჭირეთ **Save and Deploy**.

---

## 3. ცვლადების და KV-ს დაკავშირება (Bindings & Variables)

Worker-ის გვერდზე გადადით **Settings** -> **Variables and Secrets**:

### ა) KV Namespace Binding
1. ჩამოდით ქვემოთ **KV Namespace Bindings** განყოფილებამდე და დააჭირეთ **Add binding**.
2. **Variable name:** `USERS_KV` *(ზუსტად ასე დიდი ასოებით)*
3. **KV namespace:** აირჩიეთ პირველ ნაბიჯში შექმნილი `USERS_KV`.
4. დააჭირეთ **Save and Deploy**.

### ბ) Environment Variables & Secrets
დაამატეთ ორი ცვლადი:
1. `TELEGRAM_BOT_TOKEN` (Secret) -> თქვენი Telegram Bot-ის ტოკენი (მაგ. `7123456789:AAH...`)
2. `SYNC_KEY` (Secret ან Variable) -> თქვენს მიერ მოფიქრებული საიდუმლო პაროლი Python სკრიპტისთვის (მაგ. `my_super_secret_key_123`)

---

## 4. Telegram Webhook-ის გააქტიურება (1-Click)

ბრაუზერში გახსენით შემდეგი ბმული (ჩაანაცვლეთ `<YOUR_BOT_TOKEN>` და `<YOUR_WORKER_URL>`):

```text
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=https://<YOUR_WORKER_URL>.workers.dev/telegram-webhook
```

თუ ბრაუზერმა დაგიბრუნათ `{"ok": true, "result": true, "description": "Webhook was set"}`, ბოტი მზადაა!

---

## 5. შემოწმება

1. შედით თქვენს Telegram ბოტში და მიწერეთ: `/start` ან `/filter`.
2. ბოტი წამიერად გამოგიგზავნით ინტერაქტიულ ღილაკებს:
   - `[ 🏷️ ტიპის შეცვლა (იყიდება / ქირავდება / ორივე) ]`
   - `[ 👤 განმცხადებელი: ყველა / მხოლოდ მესაკუთრე / მხოლოდ სააგენტო ]`
   - `[ 💰 ფასის შეცვლა ]`
   - `[ 📐 ფართობის შეცვლა ]`
   - `[ 📍 უბნების არჩევა ]`
   - `[ 🔔 დაპაუზება / ჩართვა ]`
3. შეცვალეთ ნებისმიერი პარამეტრი — ცვლილება მომენტალურად ჩაიწერება Cloudflare KV-ში.
4. ასევე შეგიძლიათ გამოიყენოთ პირდაპირი ბრძანება `/owner` ან `/მესაკუთრე` (მაგ: `/owner owner`, `/owner agent`, `/owner all`).

---

## 6. ავტომატური დიფლოი (GitHub Actions & Safe CLI) — Zero Credential Leakage

იმისათვის, რომ `cloudflare/worker.js`-ის ყოველი განახლებისას ხელით არ მოგიწიოთ კოდის დაკოპირება ან `wrangler.toml`-ში საიდუმლო ID-ების რეპოზიტორიაში შენახვა, პროექტში ჩაშენებულია უსაფრთხო ავტო-დიფლოი სკრიპტი ([`scripts/deploy_worker.py`](../scripts/deploy_worker.py)) და GitHub Actions workflow ([`.github/workflows/deploy-worker.yml`](../.github/workflows/deploy-worker.yml)).

### როგორ მუშაობს უსაფრთხოების მექანიზმი:
- **Zero-Secret Git:** `cloudflare/wrangler.toml` და `.dev.vars` დაბლოკილია `.gitignore`-ში. რეპოზიტორიაში ინახება მხოლოდ შაბლონი [`cloudflare/wrangler.toml.example`](wrangler.toml.example).
- **Auto-Discovery & Fail-Closed:** `scripts/deploy_worker.py` Cloudflare-ის API-დან (ან ლოკალური `wrangler`-იდან) ავტომატურად კითხულობს უკვე მიბმულ `USERS_KV` და `DB` (D1) ID-ებს, ამოწმებს მათ ვალიდურობას, დროებით ქმნის `wrangler.toml`-ს (`keep_vars = true`-ით, რათა Secret-ები არ წაიშალოს), ტვირთავს Worker-ს და `finally` ბლოკში მაშინვე შლის `wrangler.toml`-ს.

### ა) GitHub Push-ზე ავტომატური დიფლოის ჩართვა (ერთჯერადი კონფიგურაცია)
GitHub რეპოზიტორიაში გადადით **Settings** -> **Secrets and variables** -> **Actions** -> **New repository secret** და დაამატეთ:

1. `CLOUDFLARE_API_TOKEN` — Cloudflare Dashboard -> **My Profile** -> **API Tokens** -> **Create Token** -> აირჩიეთ შაბლონი **Edit Cloudflare Workers** (და დარწმუნდით, რომ აქვს `Account / Workers Scripts: Edit`, `Account / Workers KV Storage: Read`, `Account / D1: Read`).
2. `CLOUDFLARE_ACCOUNT_ID` — თქვენი Cloudflare Account ID (ჩანს **Workers & Pages** მთავარ გვერდზე მარჯვენა პანელში).
3. *(არასავალდებულო, თუ უკვე გაქვთ)* `CLOUDFLARE_PROXY_URL` — თქვენი Worker-ის მისამართი (მაგ. `https://soft-smoke-33e0.iashvilisandro7.workers.dev`).

ამის შემდეგ `master`-ზე ყოველი `git push`, რომელიც შეცვლის `cloudflare/worker.js`-ს, ავტომატურად გაუშვებს ტესტებს და განაახლებს Cloudflare Worker-ს!

### ბ) ლოკალური ტერმინალიდან ერთი ბრძანებით ატვირთვა
თუ ლოკალურად გსურთ ატვირთვა, მხოლოდ ერთხელ გაიარეთ ავტორიზაცია:

```bash
npx wrangler login
```

შემდეგ კი ნებისმიერ დროს გაუშვით:

```bash
python3 scripts/deploy_worker.py
```
