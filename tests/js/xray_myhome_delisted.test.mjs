// A MyHome listing deleted from MyHome must never be shown in an X-Ray reply.
// The Worker cannot read MyHome (ADR 0004), so a reply leaves its MyHome duplicates out and
// queues an "alive" job; the job marks a deleted one with properties.delisted_at, and the
// follow-up shows the live ones. The duplicate reads skip marked rows.
// Runs the production cloudflare/worker.js offline.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadWorker } from "./load_worker.mjs";

const w = loadWorker();
const goOffline = () => { w.fetch = async () => new Response("offline", { status: 503 }); };

const CHAT = 123;
const POST =
  "იყიდება 3-ოთახიანი ბინა გლდანში, მე-6 მიკრორაიონი, 72 მ², 1 სართული, $60,000. დარეკეთ: 555000111";
const row = (id, source, price) => ({
  id, source, url: `https://example/${id}`, district: "გლდანი", street: "გლდანი - VI მ/რ",
  area_m2: 72, floor: "1", rooms: 3, price_usd: price
});
const MYHOME_DUP = row("myhome_11", "myhome", 79000);
const SSGE_DUP = row("ss_ge_5", "ss_ge", 61000);

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
const telegram = (calls, method) =>
  calls.filter(c => c.url.hostname === "api.telegram.org" && c.url.pathname.endsWith(`/${method}`));
const kv = { get: async () => null, put: async () => {} };
const env = db => ({ DB: db, GITHUB_PAT: "test-token", TELEGRAM_BOT_TOKEN: "t", SYNC_KEY: "k1", USERS_KV: kv });
const webhook = (e, update) =>
  w.handleTelegramWebhook(new Request("https://worker.example/telegram", { method: "POST", body: JSON.stringify(update) }), e);
const message = text => ({ message: { chat: { id: CHAT }, from: { id: CHAT }, text } });
const aliveJobs = db =>
  db.log.filter(e => /INSERT INTO xray_jobs/.test(e.sql) && e.params[1] === "alive");

test("the duplicate reads skip a row marked as deleted from MyHome", async () => {
  const db = recordingD1();
  await w.queryWorkerExactDuplicates(db, { id: "x", group_id: "g1", district: "გლდანი", area_m2: 72 });
  const reads = db.log.filter(e => /FROM properties WHERE (group_id|district)/.test(e.sql));
  assert.equal(reads.length, 2);
  for (const r of reads) assert.match(r.sql, /delisted_at IS NULL/);
});

const replyOf = calls => telegram(calls, "sendMessage").find(c => c.body.text.includes("რენტგენი"));
const pendingOf = db => JSON.parse(aliveJobs(db)[0].params[4]);

test("a MyHome duplicate is left out of the reply and handed to an alive job", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([[/FROM properties WHERE district IN/, () => [SSGE_DUP, MYHOME_DUP]]]);
    await webhook(env(db), message(POST));
    const reply = replyOf(calls);
    assert.ok(reply.body.text.includes("example/ss_ge_5"), reply.body.text);
    assert.ok(!reply.body.text.includes("example/myhome_11"), reply.body.text);
    assert.ok(reply.body.text.includes("myhome-ზე 1 შესაძლო დუბლიკატს ვამოწმებ"), reply.body.text);
    // One ✅/❌ row: the SS.ge line only.
    const saved = db.log.find(e => /INSERT INTO xray_results/.test(e.sql));
    assert.deepEqual(JSON.parse(saved.params[4]).map(c => c.id), ["ss_ge_5"]);
    const jobs = aliveJobs(db);
    assert.equal(jobs.length, 1);
    assert.equal(jobs[0].params[5], String(CHAT));
    const pending = pendingOf(db);
    assert.deepEqual(pending.ids, ["myhome_11"]);
    assert.equal(pending.rows[0].url, MYHOME_DUP.url);
    assert.equal(pending.price, 60000);
  } finally {
    goOffline();
  }
});

test("with only MyHome duplicates the reply does not say there is none", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([[/FROM properties WHERE district IN/, () => [MYHOME_DUP]]]);
    await webhook(env(db), message(POST));
    const text = replyOf(calls).body.text;
    assert.ok(!text.includes("დუბლიკატი სხვა პორტალზე არ იძებნება"), text);
    assert.ok(!text.includes("example/myhome_11"), text);
    assert.ok(text.includes("შესაძლო დუბლიკატს ვამოწმებ"), text);
  } finally {
    goOffline();
  }
});

test("a reply without a MyHome duplicate queues no alive job", async () => {
  serveNetwork();
  try {
    const db = recordingD1([[/FROM properties WHERE district IN/, () => [SSGE_DUP]]]);
    await webhook(env(db), message(POST));
    assert.equal(aliveJobs(db).length, 0);
  } finally {
    goOffline();
  }
});

test("when the job cannot be queued the MyHome duplicate is shown as before", async () => {
  const calls = serveNetwork();
  const realError = console.error;
  console.error = () => {};
  try {
    const db = recordingD1([[/FROM properties WHERE district IN/, () => [MYHOME_DUP]]]);
    const { GITHUB_PAT, ...noToken } = env(db);
    await webhook(noToken, message(POST));
    assert.ok(replyOf(calls).body.text.includes("example/myhome_11"));
  } finally {
    console.error = realError;
    goOffline();
  }
});

test("a re-render without a chat id leaves MyHome duplicates out and queues nothing", async () => {
  const db = recordingD1([[/FROM properties WHERE district IN/, () => [SSGE_DUP, MYHOME_DUP]]]);
  const collect = {};
  const text = await w.handleXRayQuery(POST, env(db), { collect });
  assert.ok(text.includes("example/ss_ge_5"), text);
  assert.ok(!text.includes("example/myhome_11"), text);
  assert.deepEqual(JSON.parse(JSON.stringify(collect.duplicates.map(c => c.id))), ["ss_ge_5"]);
  assert.equal(aliveJobs(db).length, 0);
});

const PENDING = {
  ids: ["myhome_11", "myhome_12"],
  rows: [
    { ...MYHOME_DUP, xray_tier: "maybe", xray_posterior: 0.51 },
    { ...row("myhome_12", "myhome", 61000) }
  ],
  price: 60000,
  target: { district: "გლდანი" }
};
const aliveJob = (result, status = "done") => [
  /FROM xray_jobs WHERE id = \?/,
  () => [{
    id: "j7", kind: "alive", chat_id: String(CHAT), status,
    target_id: JSON.stringify(PENDING), result: result ? JSON.stringify(result) : null
  }]
];
const followup = db =>
  w.handleXRayFollowup(new Request("https://worker.example/api/xray/followup", {
    method: "POST", headers: { "X-Sync-Key": "k1" }, body: JSON.stringify({ job_id: "j7" })
  }), env(db));

test("the follow-up shows only the live MyHome duplicate, with its ✅/❌ row", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([aliveJob({ live: ["myhome_12"], gone: ["myhome_11"], unknown: [] })]);
    const res = await followup(db);
    assert.equal(res.status, 200);
    const sent = telegram(calls, "sendMessage");
    assert.equal(sent.length, 1);
    const text = sent[0].body.text;
    assert.ok(text.includes('1. <a href="https://example/myhome_12">MyHome.ge</a> — <b>$61,000</b>'), text);
    assert.ok(!text.includes("myhome_11"), text);
    assert.ok(!text.includes("79,000"), text);
    assert.ok(text.includes("🚫 1 განცხადება myhome-დან წაშლილია"), text);
    const saved = db.log.find(e => /INSERT INTO xray_results/.test(e.sql));
    assert.deepEqual(JSON.parse(saved.params[4]).map(c => c.id), ["myhome_12"]);
    const rows = sent[0].body.reply_markup.inline_keyboard;
    assert.equal(rows.length, 1);
    assert.match(rows[0][0].callback_data, /^fd:.+:0:y$/);
  } finally {
    goOffline();
  }
});

test("when every MyHome duplicate was deleted the follow-up shows none and no buttons", async () => {
  const calls = serveNetwork();
  try {
    await followup(recordingD1([aliveJob({ live: [], gone: ["myhome_11", "myhome_12"], unknown: [] })]));
    const sent = telegram(calls, "sendMessage")[0];
    assert.ok(sent.body.text.includes("🚫 2 განცხადება myhome-დან წაშლილია"), sent.body.text);
    assert.ok(!sent.body.text.includes("example/"), sent.body.text);
    assert.equal(sent.body.reply_markup, undefined);
  } finally {
    goOffline();
  }
});

test("a page that did not load is shown with a warning", async () => {
  const calls = serveNetwork();
  try {
    await followup(recordingD1([aliveJob({ live: [], gone: [], unknown: ["myhome_11"] })]));
    const text = telegram(calls, "sendMessage")[0].body.text;
    assert.ok(text.includes("example/myhome_11"), text);
    assert.ok(text.includes("⚠️ 1: myhome-ის გვერდი ვერ გაიხსნა"), text);
  } finally {
    goOffline();
  }
});

test("a failed alive job shows nothing unchecked and says so", async () => {
  const calls = serveNetwork();
  try {
    await followup(recordingD1([aliveJob(null, "failed")]));
    const sent = telegram(calls, "sendMessage")[0];
    assert.ok(sent.body.text.includes("ვერ შევამოწმე, ამიტომ არ გაჩვენებ"), sent.body.text);
    assert.ok(!sent.body.text.includes("example/"), sent.body.text);
  } finally {
    goOffline();
  }
});
