// ADR 0005: photos sent to the bot are stored, one comparison job is queued per album, and the
// job's follow-up carries the ✅/❌ buttons. Runs the production cloudflare/worker.js offline:
// D1, GitHub and Telegram are fakes. Run: node --test tests/js/
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadWorker } from "./load_worker.mjs";

const w = loadWorker();
const goOffline = () => { w.fetch = async () => new Response("offline", { status: 503 }); };

const CHAT = 123;
const POST =
  "იყიდება 3-ოთახიანი ბინა გლდანში, მე-6 მიკრორაიონი, 72 მ², 1 სართული, $60,000. დარეკეთ: 555000111";

// `changes` answers the "rows changed" of a write by SQL, as D1 does; the default is one row.
function recordingD1(routes = [], changes = []) {
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
        async run() {
          log.push({ sql, params });
          const hit = changes.find(([re]) => re.test(sql));
          return { success: true, meta: { changes: hit ? hit[1] : 1 } };
        }
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
    if (u.hostname === "api.github.com") return new Response(null, { status: 204 });
    if (u.hostname === "api.telegram.org") return new Response(JSON.stringify({ ok: true }), { status: 200 });
    return new Response("offline", { status: 503 });
  };
  return calls;
}
const telegram = (calls, method) => calls.filter(c => c.url.hostname === "api.telegram.org" && c.url.pathname.endsWith(`/${method}`));
const kv = { get: async () => null, put: async () => {} };
const env = db => ({ DB: db, GITHUB_PAT: "test-token", TELEGRAM_BOT_TOKEN: "t", SYNC_KEY: "k1", USERS_KV: kv });
const webhook = (e, update) =>
  w.handleTelegramWebhook(new Request("https://worker.example/telegram", { method: "POST", body: JSON.stringify(update) }), e);
// Telegram lists the sizes of one photo from the smallest to the largest.
const photoMessage = (extra = {}) => ({
  message: {
    chat: { id: CHAT }, from: { id: CHAT }, message_id: 10,
    photo: [{ file_id: "small", file_unique_id: "u1" }, { file_id: "large", file_unique_id: "u2" }],
    ...extra
  }
});

test("a photo without a caption is stored, one job is queued, and GitHub gets only the job id", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1();
    await webhook(env(db), photoMessage());
    const photo = db.log.find(e => /INSERT OR IGNORE INTO xray_photos/.test(e.sql));
    // (batch_id, message_id, chat_id, file_id, created_at): the largest size is kept
    assert.deepEqual(photo.params.slice(0, 4), ["123:m10", 10, "123", "large"]);
    const jobs = db.log.filter(e => /INSERT INTO xray_jobs/.test(e.sql));
    assert.equal(jobs.length, 1);
    // (id, kind, phone9, listing_id, target_id, chat_id, created_at)
    assert.deepEqual(jobs[0].params.slice(1, 6), ["photo", "", null, "123:m10", "123"]);
    const sent = telegram(calls, "sendMessage");
    assert.equal(sent.length, 1, "only the acknowledgement is sent");
    assert.ok(sent[0].body.text.includes("📸 ფოტოებს ვადარებ"));
    const dispatch = calls.find(c => c.url.hostname === "api.github.com");
    assert.deepEqual(Object.keys(dispatch.body.inputs), ["job_id"]);
  } finally {
    goOffline();
  }
});

test("a later photo of an album that already has its job queues nothing and says nothing", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([], [[/INSERT OR IGNORE INTO xray_photo_batches/, 0]]);
    await webhook(env(db), photoMessage({ message_id: 11, media_group_id: "g1" }));
    const photo = db.log.find(e => /INSERT OR IGNORE INTO xray_photos/.test(e.sql));
    assert.deepEqual(photo.params.slice(0, 2), ["123:g1", 11], "the photo itself is still stored");
    assert.ok(!db.log.some(e => /INSERT INTO xray_jobs/.test(e.sql)));
    assert.ok(!calls.some(c => c.url.hostname === "api.github.com"));
    assert.equal(telegram(calls, "sendMessage").length, 0);
  } finally {
    goOffline();
  }
});

test("a captioned photo also gets the normal X-Ray of its text, and the caption is kept", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1();
    await webhook(env(db), photoMessage({ caption: POST }));
    const kinds = db.log.filter(e => /INSERT INTO xray_jobs/.test(e.sql)).map(e => e.params[1]);
    assert.ok(kinds.includes("photo"), `jobs: ${kinds}`);
    const update = db.log.find(e => /UPDATE xray_photo_batches SET caption/.test(e.sql));
    assert.deepEqual(update.params, [POST, "123:m10"]);
    const texts = telegram(calls, "sendMessage").map(c => c.body.text);
    assert.ok(texts.some(t => t.includes("📸 ფოტოებს ვადარებ")), texts.join("\n"));
    assert.ok(texts.some(t => t.includes("რენტგენი")), texts.join("\n"));
  } finally {
    goOffline();
  }
});

test("without GITHUB_PAT the user is told the comparison could not start", async () => {
  const calls = serveNetwork();
  const realError = console.error;
  console.error = () => {};
  try {
    const db = recordingD1();
    const { GITHUB_PAT, ...noToken } = env(db);
    await webhook(noToken, photoMessage());
    assert.ok(!db.log.some(e => /INSERT INTO xray_jobs/.test(e.sql)));
    assert.ok(telegram(calls, "sendMessage").pop().body.text.includes("ვერ დავიწყე"));
  } finally {
    console.error = realError;
    goOffline();
  }
});

const photoJob = (extra = {}) => [
  /FROM xray_jobs WHERE id = \?/,
  () => [{
    id: "j9", kind: "photo", chat_id: String(CHAT), status: "done",
    result: JSON.stringify({
      text: "📸 <b>ფოტოებით შედარება</b> (2 ფოტო)",
      candidates: [{ id: "myhome_1", url: "https://www.myhome.ge/ka/pr/1/" }]
    }),
    ...extra
  }]
];
const followup = (db) =>
  w.handleXRayFollowup(new Request("https://worker.example/api/xray/followup", {
    method: "POST", headers: { "X-Sync-Key": "k1" }, body: JSON.stringify({ job_id: "j9" })
  }), env(db));

test("the follow-up of a finished photo job sends its text with one ✅/❌ row per candidate", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([photoJob()]);
    const res = await followup(db);
    assert.equal(res.status, 200);
    const sent = telegram(calls, "sendMessage")[0];
    assert.equal(sent.body.chat_id, String(CHAT));
    assert.ok(sent.body.text.includes("ფოტოებით შედარება"));
    const rows = sent.body.reply_markup.inline_keyboard;
    assert.equal(rows.length, 1);
    assert.equal(rows[0][0].callback_data, "fb:j9:0:y");
    assert.equal(rows[0][1].callback_data, "fb:j9:0:n");
  } finally {
    goOffline();
  }
});

test("a failed photo job tells the user instead of leaving them waiting", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([photoJob({ status: "failed", result: null })]);
    await followup(db);
    const sent = telegram(calls, "sendMessage")[0];
    assert.ok(sent.body.text.includes("ფოტოების შედარება ვერ მოხერხდა"));
    assert.equal(sent.body.reply_markup, undefined);
  } finally {
    goOffline();
  }
});

test("a photo job with no candidates sends its text without buttons", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([photoJob({ result: JSON.stringify({ text: "ℹ️ ვერ მოიძებნა", candidates: [] }) })]);
    await followup(db);
    const sent = telegram(calls, "sendMessage")[0];
    assert.equal(sent.body.text, "ℹ️ ვერ მოიძებნა");
    assert.equal(sent.body.reply_markup, undefined);
  } finally {
    goOffline();
  }
});
