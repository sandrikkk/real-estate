// ADR 0002: the duplicate lines of an X-Ray reply carry ✅/❌ buttons, and the answers are the
// labels the match weights learn from. Runs the production cloudflare/worker.js offline:
// D1, GitHub and Telegram are fakes. Run: node --test tests/js/
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadWorker } from "./load_worker.mjs";

const w = loadWorker();
const goOffline = () => { w.fetch = async () => new Response("offline", { status: 503 }); };

const CHAT = 123;
const PHONE = "555000111";
const POST =
  "იყიდება 3-ოთახიანი ბინა გლდანში, მე-6 მიკრორაიონი, 72 მ², 1 სართული, $60,000. დარეკეთ: " + PHONE;
const LINK = "https://www.myhome.ge/ka/pr/26574188/";
const DUP = {
  id: "ss_ge_5", source: "ss_ge", url: "https://home.ss.ge/ka/udzravi-qoneba/5", district: "გლდანი",
  street: "გლდანი - VI მ/რ", area_m2: 72, floor: "1", rooms: 3, price_usd: 61000
};
const dupRoute = [/FROM properties WHERE district IN/, () => [DUP]];

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

test("each duplicate line is numbered and gets a ✅/❌ row, and the reply is saved for the answers", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([dupRoute]);
    await webhook(env(db), message(POST));
    const reply = telegram(calls, "sendMessage").find(c => c.body.text.includes("რენტგენი"));
    assert.ok(
      reply.body.text.includes('1. <a href="https://home.ss.ge/ka/udzravi-qoneba/5">SS.ge</a>'),
      reply.body.text
    );
    const [yes, no, unsure] = reply.body.reply_markup.inline_keyboard[1];
    assert.equal(yes.text, "1 ✅ იგივეა");
    assert.equal(no.text, "1 ❌ სხვაა");
    assert.equal(unsure.text, "1 🤷 ვერ ვხვდები");
    assert.match(yes.callback_data, /^fd:[0-9a-f-]{36}:0:y$/);
    assert.match(no.callback_data, /^fd:[0-9a-f-]{36}:0:n$/);
    assert.match(unsure.callback_data, /^fd:[0-9a-f-]{36}:0:u$/);
    assert.ok(bytes(yes.callback_data) <= 64, "Telegram caps callback_data at 64 bytes");
    assert.ok(bytes(unsure.callback_data) <= 64, "Telegram caps callback_data at 64 bytes");
    const saved = db.log.find(e => /INSERT INTO xray_results/.test(e.sql));
    // (id, chat_id, kind, target, candidates, owner_target_id, created_at)
    assert.equal(saved.params[0], yes.callback_data.split(":")[1]);
    assert.equal(saved.params[1], String(CHAT));
    assert.equal(saved.params[2], "dup");
    assert.equal(JSON.parse(saved.params[3]).district, "გლდანი");
    assert.deepEqual(JSON.parse(saved.params[4]), [{
      id: "ss_ge_5", source: "ss_ge", url: DUP.url, district: "გლდანი",
      street: "გლდანი - VI მ/რ", area_m2: 72, floor: "1", rooms: 3, price_usd: 61000
    }]);
  } finally {
    goOffline();
  }
});

test("the owner button stays on top, and the saved reply points at the saved flat", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([dupRoute]);
    await webhook(env(db), message(POST));
    const reply = telegram(calls, "sendMessage").find(c => c.body.text.includes("რენტგენი"));
    const rows = reply.body.reply_markup.inline_keyboard;
    assert.equal(rows.length, 2);
    const flat = db.log.find(e => /INSERT INTO xray_targets/.test(e.sql));
    assert.equal(rows[0][0].callback_data, `own:${flat.params[0]}`);
    const saved = db.log.find(e => /INSERT INTO xray_results/.test(e.sql));
    assert.equal(saved.params[5], flat.params[0]);
  } finally {
    goOffline();
  }
});

const RESULT_ID = "11111111-2222-4333-8444-555555555555";
const OWNER_TARGET_ID = "99999999-2222-4333-8444-555555555555";
const RESULT_ROW = {
  id: RESULT_ID, chat_id: String(CHAT), kind: "dup", target: "{}",
  candidates: JSON.stringify([{ id: "ss_ge_5", url: DUP.url }]),
  owner_target_id: OWNER_TARGET_ID, created_at: "2026-11-08T10:00:00.000Z"
};
const resultById = [
  /FROM xray_results WHERE id = \? AND chat_id = \?/,
  p => (p[0] === RESULT_ID && p[1] === String(CHAT) ? [RESULT_ROW] : [])
];
const answered = [/FROM xray_feedback WHERE job_id = \? AND chat_id = \?/, () => [{ candidate_id: "ss_ge_5", label: 1 }]];

test("✅ on a duplicate line records the label and marks the choice, keeping the owner button", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([resultById, answered]);
    await webhook(env(db), press(`fd:${RESULT_ID}:0:y`));
    const fb = db.log.find(e => /INSERT OR REPLACE INTO xray_feedback/.test(e.sql));
    // (job_id, candidate_id, chat_id, target_id, label, created_at)
    assert.deepEqual(fb.params.slice(0, 5), [RESULT_ID, "ss_ge_5", String(CHAT), null, 1]);
    const rows = telegram(calls, "editMessageReplyMarkup")[0].body.reply_markup.inline_keyboard;
    assert.equal(rows[0][0].callback_data, `own:${OWNER_TARGET_ID}`);
    assert.equal(rows[1].length, 3);
    assert.equal(rows[1][0].text, "1 ✅ იგივეა ✓");
  } finally {
    goOffline();
  }
});

test("✅/❌ from another chat records nothing", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([resultById, answered]);
    const update = press(`fd:${RESULT_ID}:0:y`);
    update.callback_query.message.chat.id = 999;
    await webhook(env(db), update);
    assert.ok(!db.log.some(e => /INSERT OR REPLACE INTO xray_feedback/.test(e.sql)));
    assert.equal(telegram(calls, "editMessageReplyMarkup").length, 0);
  } finally {
    goOffline();
  }
});

test("the follow-up of a finished link job carries the ✅/❌ rows and starts no job", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([
      [/FROM xray_jobs WHERE id = \?/, () => [{ id: "j3", kind: "listing", listing_id: "26574188", chat_id: String(CHAT), status: "done" }]],
      [/FROM xray_live_listings WHERE id = \?/, () => [{
        id: "myhome_26574188", source: "myhome", source_id: "26574188", url: LINK, district: "გლდანი",
        area_m2: 72, floor: "1", rooms: 3, price_usd: 61000, author_id: "4242", user_type: "agent",
        is_owner: 0, phone_number: "568443***", author_listings_count: 24,
        seen_at: new Date(Date.now() - 60 * 1000).toISOString()
      }]],
      dupRoute
    ]);
    const res = await w.handleXRayFollowup(new Request("https://worker.example/api/xray/followup", {
      method: "POST", headers: { "X-Sync-Key": "k1" }, body: JSON.stringify({ job_id: "j3" })
    }), env(db));
    assert.equal(res.status, 200);
    const sent = telegram(calls, "sendMessage")[0];
    assert.equal(sent.body.chat_id, String(CHAT));
    assert.match(sent.body.reply_markup.inline_keyboard[0][0].callback_data, /^fd:[0-9a-f-]{36}:0:y$/);
    const saved = db.log.find(e => /INSERT INTO xray_results/.test(e.sql));
    assert.equal(saved.params[1], String(CHAT));
    assert.equal(saved.params[5], null, "a link follow-up has no owner button");
    assert.ok(!db.log.some(e => /INSERT INTO xray_jobs/.test(e.sql)), "the follow-up queued another job");
  } finally {
    goOffline();
  }
});

test("a reply without duplicates has no ✅/❌ buttons and saves no result", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1();
    await webhook(env(db), message(POST));
    const reply = telegram(calls, "sendMessage").find(c => c.body.text.includes("რენტგენი"));
    assert.ok(reply.body.text.includes("დუბლიკატი სხვა პორტალზე არ იძებნება"));
    const rows = (reply.body.reply_markup && reply.body.reply_markup.inline_keyboard) || [];
    assert.ok(!rows.some(r => r.some(b => String(b.callback_data).startsWith("fd:"))));
    assert.ok(!db.log.some(e => /INSERT INTO xray_results/.test(e.sql)));
  } finally {
    goOffline();
  }
});

test("🤷 on a duplicate line stores -1 and marks it", async () => {
  const calls = serveNetwork();
  try {
    const answeredUnsure = [/FROM xray_feedback WHERE job_id = \? AND chat_id = \?/, () => [{ candidate_id: "ss_ge_5", label: -1 }]];
    const db = recordingD1([resultById, answeredUnsure]);
    await webhook(env(db), press(`fd:${RESULT_ID}:0:u`));
    const fb = db.log.find(e => /INSERT OR REPLACE INTO xray_feedback/.test(e.sql));
    assert.equal(fb.params[4], -1);
    const rows = telegram(calls, "editMessageReplyMarkup")[0].body.reply_markup.inline_keyboard;
    assert.deepEqual(rows[1].map(b => b.text), ["1 ✅ იგივეა", "1 ❌ სხვაა", "1 🤷 ვერ ვხვდები ✓"]);
  } finally {
    goOffline();
  }
});

test("an unknown answer records nothing", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([resultById, answered]);
    await webhook(env(db), press(`fd:${RESULT_ID}:0:x`));
    assert.ok(!db.log.some(e => /INSERT OR REPLACE INTO xray_feedback/.test(e.sql)));
    assert.equal(telegram(calls, "editMessageReplyMarkup").length, 0);
  } finally {
    goOffline();
  }
});

test("a prototype key is not an answer", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([resultById, answered]);
    await webhook(env(db), press(`fd:${RESULT_ID}:0:constructor`));
    assert.ok(!db.log.some(e => /INSERT OR REPLACE INTO xray_feedback/.test(e.sql)));
    assert.equal(telegram(calls, "editMessageReplyMarkup").length, 0);
  } finally {
    goOffline();
  }
});

test("a fd: press on a row with kind: 'review_random' and owner_target_id: null records the label and edits markup to 1 row of 3 buttons", async () => {
  const calls = serveNetwork();
  try {
    const REVIEW_RESULT_ID = "77777777-4444-4333-8444-555555555555";
    const reviewResultRow = {
      id: REVIEW_RESULT_ID,
      chat_id: String(CHAT),
      kind: "review_random",
      target: "{}",
      candidates: JSON.stringify([{ id: "ss_ge_5", url: DUP.url }]),
      owner_target_id: null,
      created_at: "2026-11-09T10:00:00.000Z"
    };
    const reviewResultById = [
      /FROM xray_results WHERE id = \? AND chat_id = \?/,
      p => (p[0] === REVIEW_RESULT_ID && p[1] === String(CHAT) ? [reviewResultRow] : [])
    ];
    const reviewAnswered = [
      /FROM xray_feedback WHERE job_id = \? AND chat_id = \?/,
      () => [{ candidate_id: "ss_ge_5", label: 1 }]
    ];
    const db = recordingD1([reviewResultById, reviewAnswered]);
    await webhook(env(db), press(`fd:${REVIEW_RESULT_ID}:0:y`));
    const fb = db.log.find(e => /INSERT OR REPLACE INTO xray_feedback/.test(e.sql));
    assert.deepEqual(fb.params.slice(0, 5), [REVIEW_RESULT_ID, "ss_ge_5", String(CHAT), null, 1]);
    const edits = telegram(calls, "editMessageReplyMarkup");
    assert.equal(edits.length, 1);
    const rows = edits[0].body.reply_markup.inline_keyboard;
    assert.equal(rows.length, 1);
    assert.equal(rows[0].length, 3);
    assert.equal(rows[0][0].text, "1 ✅ იგივეა ✓");
  } finally {
    goOffline();
  }
});

// Agent listing and duplicates for phone report tests
const agentListing = {
  id: "ss_ge_main", source: "ss_ge", url: "https://home.ss.ge/ka/udzravi-qoneba/main",
  district: "გლდანი", street: "გლდანი - VII მ/რ", area_m2: 72, floor: "1", rooms: 3, price_usd: 61000,
  group_id: "photo_grp_agent", is_owner: false, phone_number: "599111222"
};

const dupSure = {
  id: "myhome_dup_sure", source: "myhome", url: "https://www.myhome.ge/ka/pr/111",
  district: "გლდანი", street: "გლდანი - VII მ/რ 11", area_m2: 72, floor: "1", rooms: 3, price_usd: 60000,
  group_id: "photo_grp_agent", is_owner: true, phone_number: "599000111"
};

const dupMaybe = {
  id: "ss_ge_dup_maybe", source: "ss_ge", url: "https://home.ss.ge/ka/udzravi-qoneba/222",
  district: "გლდანი", street: "გლდანი - VII მ/რ 12", area_m2: 72, floor: "1", rooms: 3, price_usd: 62000,
  is_owner: true, phone_number: "599000222"
};

const propPhoneRoute = [
  /FROM properties WHERE phone_number IN/,
  () => [agentListing]
];
const photoGroupRoute = [
  /FROM properties WHERE group_id = \? AND id != \?/,
  () => [dupSure]
];
const dupRouteForMain = [
  /FROM properties WHERE district IN/,
  () => [dupMaybe]
];

test("phone report: numbered duplicate lines and collected candidates with their own target", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([propPhoneRoute, photoGroupRoute, dupRouteForMain]);
    const parsed = { formattedPhone: "599 11 12 22", phone9: "599111222", raw: "599111222", district: null };
    const live = { mode: "fresh", full: false, rows: [] };
    const collect = {};
    const reportText = await w.buildPhoneReport(db, parsed, live, null, collect);
    const dupSection = reportText.slice(reportText.indexOf("ამ ნომრის სხვა ბინები სხვაგანაც"));
    assert.ok(dupSection.includes("1. <a href="), "dup section contains 1. <a href=");
    assert.ok(dupSection.includes("2. <a href="), "dup section contains 2. <a href=");
    assert.equal(collect.duplicates?.length, 2);
    assert.ok(collect.duplicates.every(item => item._ref && item._ref.id === "ss_ge_main"));
    assert.equal(collect.resultKind, "phone_dup");
    assert.deepEqual(Object.keys(collect.resultTarget), []);
  } finally {
    goOffline();
  }
});

test("phone follow-up carries ✅/❌ rows and saves a phone_dup result", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([
      [/FROM xray_jobs WHERE id = \?/, () => [{
        id: "jp", kind: "phone", phone9: "599111222",
        chat_id: String(CHAT), status: "done", created_at: new Date().toISOString(), page_full: 0
      }]],
      propPhoneRoute,
      photoGroupRoute,
      dupRouteForMain
    ]);
    const res = await w.handleXRayFollowup(new Request("https://worker.example/api/xray/followup", {
      method: "POST", headers: { "X-Sync-Key": "k1" }, body: JSON.stringify({ job_id: "jp" })
    }), env(db));
    assert.equal(res.status, 200);
    const sent = telegram(calls, "sendMessage")[0];
    assert.ok(sent?.body?.reply_markup, "sendMessage has reply_markup");
    const hasFd = sent.body.reply_markup.inline_keyboard.some(row =>
      row.some(b => /^fd:[0-9a-f-]{36}:0:y$/.test(b.callback_data))
    );
    assert.ok(hasFd, "some button callback_data matches fd:...:y");
    const saved = db.log.filter(e => /INSERT INTO xray_results/.test(e.sql));
    assert.equal(saved.length, 1, "exactly one INSERT INTO xray_results");
    assert.equal(saved[0].params[2], "phone_dup");
    assert.equal(saved[0].params[5], null);
    assert.equal(JSON.parse(saved[0].params[4])[0]._ref.id, "ss_ge_main");
    assert.ok(!db.log.some(e => /INSERT INTO xray_jobs/.test(e.sql)), "no INSERT INTO xray_jobs");
  } finally {
    goOffline();
  }
});

test("a phone number sent to the bot gets ✅/❌ rows on its duplicate lines", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([propPhoneRoute, photoGroupRoute, dupRouteForMain]);
    await webhook(env(db), message("599111222"));
    const reply = telegram(calls, "sendMessage").find(c => c.body.text.includes("ამ ნომრის სხვა ბინები სხვაგანაც"));
    assert.ok(reply, "reply with duplicate section found");
    const rows = (reply.body.reply_markup && reply.body.reply_markup.inline_keyboard) || [];
    const hasFd = rows.some(r => r.some(b => String(b.callback_data).startsWith("fd:")));
    assert.ok(hasFd, "has an fd: button");
    const saved = db.log.find(e => /INSERT INTO xray_results/.test(e.sql));
    assert.ok(saved, "saved xray_results row exists");
    assert.equal(saved.params[2], "phone_dup");
  } finally {
    goOffline();
  }
});

test("a phone report without duplicates has no ✅/❌ buttons and saves no result", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([
      [/FROM xray_jobs WHERE id = \?/, () => [{
        id: "jp", kind: "phone", phone9: "599111222",
        chat_id: String(CHAT), status: "done", created_at: new Date().toISOString(), page_full: 0
      }]],
      propPhoneRoute
    ]);
    const res = await w.handleXRayFollowup(new Request("https://worker.example/api/xray/followup", {
      method: "POST", headers: { "X-Sync-Key": "k1" }, body: JSON.stringify({ job_id: "jp" })
    }), env(db));
    assert.equal(res.status, 200);
    const sent = telegram(calls, "sendMessage")[0];
    const rows = (sent?.body?.reply_markup && sent.body.reply_markup.inline_keyboard) || [];
    assert.ok(!rows.some(r => r.some(b => String(b.callback_data).startsWith("fd:"))));
    assert.ok(!db.log.some(e => /INSERT INTO xray_results/.test(e.sql)));
  } finally {
    goOffline();
  }
});

test("phone report: a hint under the header, and the └ listing is a link", async () => {
  serveNetwork();
  try {
    const db = recordingD1([propPhoneRoute, photoGroupRoute, dupRouteForMain]);
    const parsed = { formattedPhone: "599 11 12 22", phone9: "599111222", raw: "599111222", district: null };
    const live = { mode: "fresh", full: false, rows: [] };
    const reportText = await w.buildPhoneReport(db, parsed, live, null, {});
    const lines = reportText.split("\n");
    const h = lines.indexOf("🎯 <b>ამ ნომრის სხვა ბინები სხვაგანაც:</b>");
    assert.ok(h >= 0, "section header present");
    assert.equal(lines[h + 1], "ℹ️ ეს ამ ნომრის <b>სხვა</b> ბინებია, არა შენი პოსტი. ✅/❌: ზედა ხაზი და მის ქვეშ └ ერთი ბინაა?");
    const refLink = `<a href="${agentListing.url}">$${agentListing.price_usd.toLocaleString()}</a>`;
    assert.equal(reportText.split(refLink).length - 1, 2, "both └ lines link the seller's listing");
  } finally {
    goOffline();
  }
});
