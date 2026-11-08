// ADR 0003 stage 2: the owner-search button, the word "მეპატრონე", the owner job's
// follow-up and the ✅/❌ feedback, in the production cloudflare/worker.js. D1, GitHub and
// Telegram are fakes. Run: node --test tests/js/
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadWorker } from "./load_worker.mjs";

const w = loadWorker();
const goOffline = () => { w.fetch = async () => new Response("offline", { status: 503 }); };

const CHAT = 123;
const PHONE = "555000111";
const GLDANI_POST =
  "იყიდება ბინა , გლდანის მე-6მიკრო, სამ ოთახიანი ჩეხური პროექტის, სართული 1 ,ბინას აქვს აივანი. " +
  "დეტალებზე დამირეკეთ:" + PHONE;

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
const message = text => ({ message: { chat: { id: CHAT }, from: { id: CHAT }, text } });
const press = data => ({ callback_query: { id: "cb1", data, message: { chat: { id: CHAT }, message_id: 77 } } });
const bytes = s => new TextEncoder().encode(s).length;

const TARGET_ROW = {
  id: "11111111-2222-4333-8444-555555555555",
  chat_id: String(CHAT),
  target: JSON.stringify({ district: "გლდანი", micro: 6, rooms: 3, floor: "1", phone9: PHONE, deal_type: "sale" }),
  created_at: "2026-11-08T10:00:00.000Z"
};
const targetById = [/FROM xray_targets WHERE id = \? AND chat_id = \?/, p => (p[0] === TARGET_ROW.id && p[1] === String(CHAT) ? [TARGET_ROW] : [])];
const latestTarget = [/FROM xray_targets WHERE chat_id = \? ORDER BY/, p => (p[0] === String(CHAT) ? [TARGET_ROW] : [])];

test("an X-Ray of a post that names a flat gets the owner-search button, and the flat is saved", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1();
    await webhook(env(db), message(GLDANI_POST));
    const reply = telegram(calls, "sendMessage").find(c => c.body.text.includes("ტელეფონის რენტგენი"));
    const button = reply.body.reply_markup.inline_keyboard[0][0];
    assert.equal(button.text, "🔍 მესაკუთრის ძებნა");
    assert.match(button.callback_data, /^own:[0-9a-f-]{36}$/);
    assert.ok(bytes(button.callback_data) <= 64, "Telegram caps callback_data at 64 bytes");
    const saved = db.log.find(e => /INSERT INTO xray_targets/.test(e.sql));
    assert.equal(saved.params[0], button.callback_data.slice(4));
    assert.equal(saved.params[1], String(CHAT));
    const target = JSON.parse(saved.params[2]);
    assert.equal(target.district, "გლდანი");
    assert.equal(target.micro, 6);
    assert.equal(target.rooms, 3);
    assert.equal(target.floor, "1");
    assert.equal(target.phone9, PHONE);
    assert.equal(target.deal_type, "sale");
  } finally {
    goOffline();
  }
});

test("a bare phone number names no flat, so it gets no owner-search button", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1();
    await webhook(env(db), message(PHONE));
    const reply = telegram(calls, "sendMessage").find(c => c.body.text.includes("ტელეფონის რენტგენი"));
    assert.equal(reply.body.reply_markup, undefined);
    assert.ok(!db.log.some(e => /INSERT INTO xray_targets/.test(e.sql)));
  } finally {
    goOffline();
  }
});

test("the button queues an owner job, and GitHub gets only the opaque job id", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([targetById]);
    await webhook(env(db), press(`own:${TARGET_ROW.id}`));
    const job = db.log.find(e => /INSERT INTO xray_jobs/.test(e.sql));
    // (id, kind, phone9, listing_id, target_id, chat_id, created_at)
    assert.deepEqual([job.params[1], job.params[4], job.params[5]], ["owner", TARGET_ROW.id, String(CHAT)]);
    const dispatch = calls.find(c => c.url.hostname === "api.github.com");
    assert.deepEqual(Object.keys(dispatch.body.inputs), ["job_id"]);
    const sent = telegram(calls, "sendMessage").pop();
    assert.ok(sent.body.text.includes("მესაკუთრეს ვეძებ:</b> გლდანი, მე-6 მიკრო, 3 ოთახი, 1 სართ."));
  } finally {
    goOffline();
  }
});

test("another chat's button does nothing", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([targetById]);
    const update = press(`own:${TARGET_ROW.id}`);
    update.callback_query.message.chat.id = 999;
    await webhook(env(db), update);
    assert.ok(!db.log.some(e => /INSERT INTO xray_jobs/.test(e.sql)));
    assert.ok(!calls.some(c => c.url.hostname === "api.github.com"));
  } finally {
    goOffline();
  }
});

test("the word მეპატრონე searches for the last flat this chat X-Rayed", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([latestTarget]);
    await webhook(env(db), message("მეპატრონე"));
    const job = db.log.find(e => /INSERT INTO xray_jobs/.test(e.sql));
    assert.equal(job.params[4], TARGET_ROW.id);
    assert.ok(telegram(calls, "sendMessage").pop().body.text.includes("მესაკუთრეს ვეძებ"));
  } finally {
    goOffline();
  }
});

test("the word without any earlier X-Ray explains what to send first", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1();
    await webhook(env(db), message("მესაკუთრე"));
    assert.ok(!db.log.some(e => /INSERT INTO xray_jobs/.test(e.sql)));
    assert.ok(telegram(calls, "sendMessage").pop().body.text.includes("ჯერ ჩამიგდე ბინის პოსტი"));
  } finally {
    goOffline();
  }
});

const OWNER_JOB = {
  id: "22222222-3333-4444-8555-666666666666", kind: "owner", chat_id: String(CHAT), status: "done",
  target_id: TARGET_ROW.id,
  result: JSON.stringify({
    text: "🏠 <b>მესაკუთრის ძებნა:</b> გლდანი\n\n🔎 <b>სავარაუდო:</b>\n1. SS.ge\n2. MyHome",
    candidates: [{ id: "ss_ge_10", url: "https://home.ss.ge/10" }, { id: "myhome_1", url: "https://www.myhome.ge/ka/pr/1/" }]
  })
};

test("the owner job's follow-up carries its text and one ✅/❌ row per candidate", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([[/FROM xray_jobs WHERE id = \?/, () => [OWNER_JOB]]]);
    const res = await w.handleXRayFollowup(new Request("https://worker.example/api/xray/followup", {
      method: "POST", headers: { "X-Sync-Key": "k1" }, body: JSON.stringify({ job_id: OWNER_JOB.id })
    }), env(db));
    assert.equal(res.status, 200);
    const sent = telegram(calls, "sendMessage").pop();
    assert.equal(sent.body.chat_id, String(CHAT));
    assert.ok(sent.body.text.startsWith("🏠 <b>მესაკუთრის ძებნა:</b>"));
    const rows = sent.body.reply_markup.inline_keyboard;
    assert.equal(rows.length, 2);
    assert.deepEqual(rows[0].map(b => b.text), ["1 ✅ იგივეა", "1 ❌ სხვაა"]);
    for (const b of rows.flat()) assert.ok(bytes(b.callback_data) <= 64, b.callback_data);
  } finally {
    goOffline();
  }
});

test("a failed owner job tells the user", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([[/FROM xray_jobs WHERE id = \?/, () => [{ ...OWNER_JOB, status: "failed", result: null }]]]);
    await w.handleXRayFollowup(new Request("https://worker.example/api/xray/followup", {
      method: "POST", headers: { "X-Sync-Key": "k1" }, body: JSON.stringify({ job_id: OWNER_JOB.id })
    }), env(db));
    assert.ok(telegram(calls, "sendMessage").pop().body.text.includes("მესაკუთრის ძებნა ვერ მოხერხდა"));
  } finally {
    goOffline();
  }
});

test("✅/❌ records the label for that candidate and marks the choice on the buttons", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([
      [/FROM xray_jobs WHERE id = \? AND chat_id = \?/, p => (p[1] === String(CHAT) ? [OWNER_JOB] : [])],
      [/FROM xray_feedback WHERE job_id/, () => [{ candidate_id: "myhome_1", label: 0 }]]
    ]);
    await webhook(env(db), press(`fb:${OWNER_JOB.id}:1:n`));
    const saved = db.log.find(e => /INTO xray_feedback/.test(e.sql));
    assert.deepEqual(saved.params.slice(0, 5), [OWNER_JOB.id, "myhome_1", String(CHAT), TARGET_ROW.id, 0]);
    const edit = telegram(calls, "editMessageReplyMarkup").pop();
    assert.equal(edit.body.message_id, 77);
    assert.deepEqual(edit.body.reply_markup.inline_keyboard[1].map(b => b.text), ["2 ✅ იგივეა", "2 ❌ სხვაა ✓"]);
  } finally {
    goOffline();
  }
});

test("✅/❌ on another chat's result records nothing", async () => {
  serveNetwork();
  try {
    const db = recordingD1([[/FROM xray_jobs WHERE id = \? AND chat_id = \?/, p => (p[1] === String(CHAT) ? [OWNER_JOB] : [])]]);
    const update = press(`fb:${OWNER_JOB.id}:0:y`);
    update.callback_query.message.chat.id = 999;
    await webhook(env(db), update);
    assert.ok(!db.log.some(e => /INTO xray_feedback/.test(e.sql)));
  } finally {
    goOffline();
  }
});
