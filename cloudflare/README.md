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
   - `[ 💰 ფასის შეცვლა ]`
   - `[ 📐 ფართობის შეცვლა ]`
   - `[ 📍 უბნების არჩევა ]`
   - `[ 🔔 დაპაუზება / ჩართვა ]`
3. შეცვალეთ ნებისმიერი პარამეტრი — ცვლილება მომენტალურად ჩაიწერება Cloudflare KV-ში.
