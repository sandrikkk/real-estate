// A MyHome listing deleted from MyHome must not keep coming back in X-Ray replies.
// The Worker cannot read MyHome (ADR 0004), so a reply that shows a MyHome duplicate queues an
// "alive" job; the job marks a deleted one with properties.delisted_at, and the Worker's
// duplicate reads skip marked rows. Runs the production cloudflare/worker.js offline.
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

test("a reply showing a MyHome duplicate queues an alive job with the reply's ids in order", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([[/FROM properties WHERE district IN/, () => [SSGE_DUP, MYHOME_DUP]]]);
    await webhook(env(db), message(POST));
    const reply = telegram(calls, "sendMessage").find(c => c.body.text.includes("რენტგენი"));
    const order = ["ss_ge_5", "myhome_11"].sort(
      (a, b) => reply.body.text.indexOf(`example/${a}`) - reply.body.text.indexOf(`example/${b}`)
    );
    const jobs = aliveJobs(db);
    assert.equal(jobs.length, 1);
    // (id, kind, phone9, listing_id, target_id, chat_id, created_at)
    assert.deepEqual(JSON.parse(jobs[0].params[4]), order);
    assert.equal(jobs[0].params[5], String(CHAT));
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

const aliveJob = result => [
  /FROM xray_jobs WHERE id = \?/,
  () => [{ id: "j7", kind: "alive", chat_id: String(CHAT), status: "done", result: JSON.stringify(result) }]
];
const followup = db =>
  w.handleXRayFollowup(new Request("https://worker.example/api/xray/followup", {
    method: "POST", headers: { "X-Sync-Key": "k1" }, body: JSON.stringify({ job_id: "j7" })
  }), env(db));

test("the alive follow-up names a deleted listing, without buttons", async () => {
  const calls = serveNetwork();
  try {
    const text = "🚫 <b>myhome-ზე შევამოწმე: ეს განცხადება წაშლილია (აღარ არის აქტიური)</b>\n1. MyHome — $79,000";
    const res = await followup(recordingD1([aliveJob({ gone: ["myhome_11"], text })]));
    assert.equal(res.status, 200);
    const sent = telegram(calls, "sendMessage");
    assert.equal(sent.length, 1);
    assert.equal(sent[0].body.text, text);
    assert.equal(sent[0].body.reply_markup, undefined);
  } finally {
    goOffline();
  }
});

test("an alive job that found nothing deleted sends no message", async () => {
  const calls = serveNetwork();
  try {
    const res = await followup(recordingD1([aliveJob({ gone: [], text: null })]));
    assert.equal(res.status, 200);
    assert.equal(telegram(calls, "sendMessage").length, 0);
  } finally {
    goOffline();
  }
});
