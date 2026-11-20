import { test } from "node:test";
import assert from "node:assert/strict";
import { loadWorker } from "./load_worker.mjs";

const w = loadWorker();
const goOffline = () => { w.fetch = async () => new Response("offline", { status: 503 }); };

const CHAT = 123;

function recordingD1(routes = []) {
  const log = [];
  return {
    log,
    prepare(sql) {
      let params = [];
      const rows = () => {
        const route = routes.find(([re]) => re.test(sql));
        return route ? route[1](params) : [];
      };
      const stmt = {
        bind(...p) { params = p; return stmt; },
        async all() { log.push({ sql, params }); return { results: rows() }; },
        async first() { log.push({ sql, params }); return rows()[0] || null; },
        async run() { log.push({ sql, params }); return { success: true }; }
      };
      return stmt;
    }
  };
}

function serveNetwork() {
  const calls = [];
  w.fetch = async (url, init = {}) => {
    const u = new URL(String(url));
    calls.push({ url: u, body: init.body ? JSON.parse(init.body) : null });
    if (u.hostname === "api.telegram.org") return new Response(JSON.stringify({ ok: true }), { status: 200 });
    return new Response("offline", { status: 503 });
  };
  return calls;
}

const telegram = (calls, method) => calls.filter(c => c.url.hostname === "api.telegram.org" && c.url.pathname.endsWith(`/${method}`));
const kv = { get: async () => null, put: async () => {} };
const env = db => ({ DB: db, TELEGRAM_BOT_TOKEN: "t", USERS_KV: kv });
const webhook = (e, update) =>
  w.handleTelegramWebhook(new Request("https://worker.example/telegram", { method: "POST", body: JSON.stringify(update) }), e);
const press = (data, chatId = CHAT) => ({ callback_query: { id: "cb1", data, message: { chat: { id: chatId }, message_id: 77 } } });
const message = (text, chatId = CHAT) => ({ message: { chat: { id: chatId }, from: { id: chatId }, text } });

test("w: inserts a row and edits the markup to uw:", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([
      [/FROM properties WHERE id = \?/, p => (p[0] === "myhome_123" ? [{ id: "myhome_123", price_usd: 50000 }] : [])],
      [/COUNT\(\*\).*FROM watchlist/, () => [{ n: 0 }]]
    ]);
    await webhook(env(db), press("w:myhome_123"));

    const insert = db.log.find(e => /INSERT INTO watchlist/i.test(e.sql));
    assert.ok(insert, "watchlist insert was logged");
    assert.equal(insert.params[0], String(CHAT));
    assert.equal(insert.params[1], "myhome_123");
    assert.equal(insert.params[2], 50000);

    const edits = telegram(calls, "editMessageReplyMarkup");
    assert.equal(edits.length, 1);
    assert.deepEqual(edits[0].body.reply_markup.inline_keyboard, [
      [{ text: "🗑 თვალყურის მოხსნა", callback_data: "uw:myhome_123" }]
    ]);

    const answers = telegram(calls, "answerCallbackQuery");
    assert.equal(answers.length, 1);
    assert.equal(answers[0].body.text, "👁 თვალყურს ვადევნებ");
  } finally {
    goOffline();
  }
});

test("the 31st flat is refused", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([
      [/FROM properties WHERE id = \?/, p => [{ id: p[0], price_usd: 50000 }]],
      [/COUNT\(\*\).*FROM watchlist/, () => [{ n: 30 }]]
    ]);
    await webhook(env(db), press("w:myhome_999"));

    const insert = db.log.find(e => /INSERT INTO watchlist/i.test(e.sql));
    assert.equal(insert, undefined, "no insert performed when limit reached");

    const answers = telegram(calls, "answerCallbackQuery");
    assert.equal(answers.length, 1);
    assert.equal(answers[0].body.text, "მაქსიმუმ 30 ბინა. /watchlist-ში მოხსენი რომელიმე");
  } finally {
    goOffline();
  }
});

test("an unknown listing gets the toast", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([
      [/FROM properties WHERE id = \?/, () => []]
    ]);
    await webhook(env(db), press("w:nonexistent_listing"));

    const insert = db.log.find(e => /INSERT INTO watchlist/i.test(e.sql));
    assert.equal(insert, undefined);

    const answers = telegram(calls, "answerCallbackQuery");
    assert.equal(answers.length, 1);
    assert.equal(answers[0].body.text, "ბინა ვერ ვიპოვე");
  } finally {
    goOffline();
  }
});

test("uw: sets the status to removed", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1();
    await webhook(env(db), press("uw:myhome_123"));

    const update = db.log.find(e => /UPDATE watchlist SET status='removed'/i.test(e.sql));
    assert.ok(update, "update statement was executed");
    assert.equal(update.params[1], String(CHAT));
    assert.equal(update.params[2], "myhome_123");

    const edits = telegram(calls, "editMessageReplyMarkup");
    assert.equal(edits.length, 1);
    assert.deepEqual(edits[0].body.reply_markup.inline_keyboard, [
      [{ text: "👁 თვალყური ადევნე", callback_data: "w:myhome_123" }]
    ]);

    const answers = telegram(calls, "answerCallbackQuery");
    assert.equal(answers.length, 1);
    assert.equal(answers[0].body.text, "თვალყურის დევნება მოიხსნა");
  } finally {
    goOffline();
  }
});

test("/watchlist lists only the active rows of this chat", async () => {
  const calls = serveNetwork();
  try {
    const rows = [
      {
        listing_id: "myhome_1",
        last_price_usd: 60000,
        created_at: "2026-11-20T10:00:00Z",
        url: "https://myhome.ge/pr/1",
        district: "საბურთალო",
        area_m2: 55
      },
      {
        listing_id: "ss_ge_2",
        last_price_usd: 80000,
        created_at: "2026-11-20T09:00:00Z",
        url: "https://ss.ge/pr/2",
        district: "ვაკე",
        area_m2: 70
      }
    ];
    const db = recordingD1([
      [/FROM watchlist w.*WHERE w\.chat_id = \? AND w\.status = 'active'/i, p => {
        assert.equal(p[0], String(CHAT));
        return rows;
      }]
    ]);

    await webhook(env(db), message("/watchlist", CHAT));

    const sends = telegram(calls, "sendMessage");
    assert.equal(sends.length, 1);
    const body = sends[0].body;
    assert.equal(body.chat_id, CHAT);
    assert.ok(body.text.includes("👁 <b>თვალყურს ადევნებ: 2 ბინას</b>"));
    assert.ok(body.text.includes('1. <a href="https://myhome.ge/pr/1">საბურთალო, 55 მ²</a> — $'));
    assert.ok(body.text.includes('2. <a href="https://ss.ge/pr/2">ვაკე, 70 მ²</a> — $'));
    assert.equal(body.disable_web_page_preview, true);

    const buttons = body.reply_markup.inline_keyboard[0];
    assert.equal(buttons.length, 2);
    assert.deepEqual(buttons[0], { text: "🗑 1", callback_data: "uwl:myhome_1" });
    assert.deepEqual(buttons[1], { text: "🗑 2", callback_data: "uwl:ss_ge_2" });
  } finally {
    goOffline();
  }
});

test("uwl: re-renders the list", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([
      [/FROM watchlist w.*WHERE w\.chat_id = \? AND w\.status = 'active'/i, () => []]
    ]);
    await webhook(env(db), press("uwl:myhome_1"));

    const update = db.log.find(e => /UPDATE watchlist SET status='removed'/i.test(e.sql));
    assert.ok(update);

    const edits = telegram(calls, "editMessageText");
    assert.equal(edits.length, 1);
    assert.ok(edits[0].body.text.includes("👁 ჯერ არცერთ ბინას არ ადევნებ თვალყურს"));
  } finally {
    goOffline();
  }
});

test("a D1 error gives a toast and does not throw", async () => {
  const calls = serveNetwork();
  try {
    const failingDb = {
      prepare() {
        throw new Error("D1 connection lost");
      }
    };
    await webhook(env(failingDb), press("w:myhome_123"));

    const answers = telegram(calls, "answerCallbackQuery");
    assert.equal(answers.length, 1);
    assert.equal(answers[0].body.text, "ახლა ვერ მოხერხდა, სცადე რამდენიმე წუთში");
  } finally {
    goOffline();
  }
});
