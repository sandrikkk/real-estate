// ADR 0003 stage 1 with ADR 0004: the phone X-Ray shows MyHome results found by a GitHub
// Actions job, queues that job when no recent result exists, and keeps the post's flat details.
// Runs the production cloudflare/worker.js offline: D1, GitHub and Telegram here are fakes.
// Run: node --test tests/js/
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadWorker } from "./load_worker.mjs";

const w = loadWorker();
const goOffline = () => { w.fetch = async () => new Response("offline", { status: 503 }); };

// The user's example post (2026-11-07) with a made-up phone number. It has no area,
// so it reaches the phone branch, not the Facebook-post branch.
const PHONE = "555000111";
const PHONE_FMT = "+995 555 00 01 11";
const CHAT = 123;
const GLDANI_POST =
  "იყიდება ბინა , გლდანის მე-6მიკრო, სამ ოთახიანი ჩეხური პროექტის, სართული 1 ,ბინას აქვს აივანი და " +
  "მოწესრიგებული სარდაფი, გაკეთებულია ცეტრალური გათბობა, რჩება ავეჯი და ტექნიკა. \nდეტალებზე დამირეკეთ:" +
  PHONE;

// D1 stand-in that records every statement, so a test can check what was written where.
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

// Records outgoing requests; GitHub's dispatch answers 204, Telegram answers ok.
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

const minutesAgo = m => new Date(Date.now() - m * 60 * 1000).toISOString();
// A row as scripts/xray_lookup.py saves it in xray_live_listings.
function liveRow(id, extra = {}) {
  return {
    id: `myhome_${id}`, source: "myhome", source_id: String(id), phone9: PHONE,
    url: `https://www.myhome.ge/ka/pr/${id}/`, district: "გლდანი", street: "გლდანი - VI მ/რ",
    area_m2: 70 + (id % 5), floor: "1", rooms: 3, price_usd: 60000 + id, author_id: "4242",
    user_type: "agent", is_owner: id === 1 ? 0 : null, seen_at: minutesAgo(1), ...extra
  };
}
const D1_ROW = {
  id: "myhome_2", source: "myhome", source_id: "2", url: "https://www.myhome.ge/ka/pr/2/",
  district: "გლდანი", street: "გლდანი - VI მ/რ", area_m2: 72, floor: "1", rooms: 3, price_usd: 60002,
  phone_number: PHONE_FMT, is_owner: 0, user_type: "agent", author_id: "4242", author_listings_count: 2
};
const byPhone = [/FROM properties WHERE phone_number IN/, () => [D1_ROW]];
const doneJob = (extra = {}) => [
  /FROM xray_jobs WHERE phone9/,
  () => [{ id: "j1", phone9: PHONE, chat_id: String(CHAT), status: "done", found: 2, page_full: 0,
    created_at: minutesAgo(2), done_at: minutesAgo(1), ...extra }]
];
const jobRows = rows => [/FROM xray_live_listings WHERE phone9 = \? AND seen_at/, () => rows];
const savedRows = rows => [/FROM xray_live_listings WHERE phone9 = \? ORDER/, () => rows];
const ENV = db => ({ DB: db, GITHUB_PAT: "test-token", TELEGRAM_BOT_TOKEN: "t", SYNC_KEY: "k1" });

test("a post without an area keeps its flat details as a hint on the phone lookup", () => {
  const p = w.parseXRayInput(GLDANI_POST);
  assert.equal(p.kind, "phone");
  assert.equal(p.phone9, PHONE);
  assert.equal(p.flat.district, "გლდანი");
  assert.equal(p.flat.micro, 6);
  assert.equal(p.flat.rooms, 3);
  assert.equal(p.flat.floor, "1");
  // The hint is not a parsed fact: the golden set scores the top-level fields only.
  assert.equal(p.district, undefined);
});

test("micro-district: Arabic, Roman and spelled-out forms", () => {
  const cases = [
    ["გლდანის მე-6მიკრო", 6],
    ["გლდანი - VI მ/რ", 6],
    ["6 მ/რ", 6],
    ["მე-6 მიკრორაიონი", 6],
    ["6-ე მიკრორაიონი", 6],
    ["გლდანი - I მ/რ 23ბ", 1],
    ["თემქაზე, 3 მ/რ, 4 კვარტალი", 3],
    ["ვაკე, ჭავჭავაძის გამზ. 6", null],
    ["", null]
  ];
  for (const [text, want] of cases) assert.equal(w.extractMicroDistrict(text), want, text);
});

test("matcher: a different micro-district rejects, the same one corroborates", () => {
  const target = { district: "გლდანი", micro: 6, rooms: 3, floor: "1", area_m2: 72 };
  const other = { district: "გლდანი", street: "გლდანი - III მ/რ", rooms: 3, floor: "1", area_m2: 72 };
  const same = { district: "გლდანი", street: "გლდანი - VI მ/რ 23ბ", rooms: null, floor: "1", area_m2: 72 };
  assert.equal(w.compareLocation(target, other), "conflict");
  assert.equal(w.workerCandidateMatchesTarget(target, other), false);
  assert.equal(w.compareLocation(target, same), "agree");
  assert.equal(w.workerCandidateMatchesTarget(target, same), true);
});

test("a recent lookup's MyHome rows merge with D1 rows without duplicates, and no job is queued", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([byPhone, doneJob(), jobRows([liveRow(1), liveRow(2)])]);
    const report = await w.handleXRayQuery(GLDANI_POST, ENV(db), { chatId: CHAT });
    assert.match(report, /ნაპოვნი განცხადებები \(2\)/);
    assert.equal((report.match(/myhome\.ge\/ka\/pr\/2\//g) || []).length, 1, "the same listing was listed twice");
    assert.ok(report.includes("myhome.ge/ka/pr/1/"), "the MyHome-only listing is missing");
    assert.ok(report.includes("myhome-ზე ამ ნომერზე <b>2</b>"));
    assert.ok(report.includes(`https://www.myhome.ge/ka/s/udzravi-qoneba/?q=${PHONE}`));
    assert.ok(!calls.some(c => c.url.hostname === "api.github.com"), "a fresh result should not start a new job");
    assert.ok(!db.log.some(e => /INSERT INTO xray_jobs/.test(e.sql)));
  } finally {
    goOffline();
  }
});

test("a full first MyHome page shows N+", async () => {
  serveNetwork();
  try {
    const rows = Array.from({ length: 24 }, (_, i) => liveRow(100 + i));
    const db = recordingD1([doneJob({ page_full: 1, found: 24 }), jobRows(rows)]);
    const report = await w.handleXRayQuery(GLDANI_POST, ENV(db), { chatId: CHAT });
    assert.ok(report.includes("myhome-ზე ამ ნომერზე <b>24+</b>"));
    assert.match(report, /ნაპოვნი განცხადებები \(24\+\)/);
  } finally {
    goOffline();
  }
});

test("without a recent lookup a job is queued, and GitHub gets only the opaque job id", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([byPhone]);
    const report = await w.handleXRayQuery(GLDANI_POST, ENV(db), { chatId: CHAT });
    const insert = db.log.find(e => /INSERT INTO xray_jobs/.test(e.sql));
    assert.ok(insert, "no job was recorded");
    // (id, kind, phone9, listing_id, target_id, chat_id, created_at)
    assert.equal(insert.params[1], "phone");
    assert.equal(insert.params[2], PHONE);
    assert.equal(insert.params[3], null);
    assert.equal(insert.params[4], null);
    assert.equal(insert.params[5], String(CHAT));
    const dispatch = calls.find(c => c.url.hostname === "api.github.com");
    assert.ok(dispatch && dispatch.url.pathname.endsWith("/actions/workflows/xray-lookup.yml/dispatches"));
    assert.deepEqual(Object.keys(dispatch.body.inputs), ["job_id"]);
    assert.equal(dispatch.body.inputs.job_id, insert.params[0]);
    assert.ok(!JSON.stringify(dispatch.body).includes(PHONE.slice(3)), "the phone number reached GitHub");
    assert.ok(!JSON.stringify(dispatch.body).includes(String(CHAT)), "the chat id reached GitHub");
    assert.ok(report.includes("🔄 myhome-ზე ვეძებ"));
    assert.ok(report.includes("myhome.ge/ka/pr/2/"), "the D1 answer should not wait for the job");
  } finally {
    goOffline();
  }
});

test("a lookup older than the reuse window is repeated", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([byPhone, doneJob({ created_at: minutesAgo(90), done_at: minutesAgo(89) })]);
    await w.handleXRayQuery(GLDANI_POST, ENV(db), { chatId: CHAT });
    assert.ok(calls.some(c => c.url.hostname === "api.github.com"), "a stale result was reused");
  } finally {
    goOffline();
  }
});

test("without a GitHub token the reply says MyHome could not be checked", async () => {
  serveNetwork();
  try {
    const db = recordingD1([byPhone]);
    const report = await w.handleXRayQuery(GLDANI_POST, { DB: db }, { chatId: CHAT });
    assert.ok(report.includes("myhome-ის შედეგი ახლა ვერ მივიღეთ, ნაჩვენებია მხოლოდ ჩვენი ბაზა"));
    assert.ok(report.includes("myhome.ge/ka/pr/2/"));
    assert.ok(!db.log.some(e => /INSERT INTO xray_jobs/.test(e.sql)), "a job nobody will run was recorded");
  } finally {
    goOffline();
  }
});

test("rows from an older lookup are shown as last seen while a new one runs", async () => {
  serveNetwork();
  try {
    const db = recordingD1([savedRows([liveRow(5, { seen_at: minutesAgo(600) })])]);
    const report = await w.handleXRayQuery(GLDANI_POST, ENV(db), { chatId: CHAT });
    assert.ok(report.includes("myhome.ge/ka/pr/5/"));
    assert.ok(report.includes("ბოლოს ნანახიც ჩანს"));
  } finally {
    goOffline();
  }
});

test("nothing from the lookup and nothing in D1 says so plainly", async () => {
  serveNetwork();
  try {
    const db = recordingD1([doneJob({ found: 0 })]);
    const report = await w.handleXRayQuery(GLDANI_POST, ENV(db), { chatId: CHAT });
    assert.ok(report.includes("ვერც myhome-ზე და ვერც ჩვენს ბაზაში"));
  } finally {
    goOffline();
  }
});

test("the phone X-Ray never inserts into properties", async () => {
  serveNetwork();
  try {
    for (const routes of [[byPhone], [doneJob(), jobRows([liveRow(1)])]]) {
      const db = recordingD1(routes);
      await w.handleXRayQuery(GLDANI_POST, ENV(db), { chatId: CHAT });
      assert.ok(!db.log.some(e => /INSERT\b.*\bINTO properties/i.test(e.sql)));
      for (const e of db.log) assert.ok(!e.params.includes(undefined), "D1 rejects undefined bindings");
    }
  } finally {
    goOffline();
  }
});

test("follow-up: the job's result goes to the chat that asked; a wrong key is refused", async () => {
  const req = (key, body) => new Request("https://worker.example/api/xray/followup", {
    method: "POST", headers: { "X-Sync-Key": key, "content-type": "application/json" }, body: JSON.stringify(body)
  });
  const calls = serveNetwork();
  try {
    const db = recordingD1([
      [/FROM xray_jobs WHERE id = \?/, () => [{ id: "j1", phone9: PHONE, chat_id: "777", status: "done",
        page_full: 0, created_at: minutesAgo(2), done_at: minutesAgo(1) }]],
      jobRows([liveRow(1)])
    ]);
    assert.equal((await w.__worker.fetch(req("wrong", { job_id: "j1" }), ENV(db))).status, 401);
    const res = await w.__worker.fetch(req("k1", { job_id: "j1" }), ENV(db));
    assert.equal(res.status, 200);
    const sent = calls.find(c => c.url.hostname === "api.telegram.org");
    assert.equal(sent.body.chat_id, "777");
    assert.ok(sent.body.text.includes("myhome-ის შედეგი"));
    assert.ok(sent.body.text.includes("myhome.ge/ka/pr/1/"));
  } finally {
    goOffline();
  }
});

test("follow-up: a failed job tells the user instead of leaving them waiting", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([[/FROM xray_jobs WHERE id = \?/, () => [{ id: "j2", phone9: PHONE, chat_id: "777", status: "failed" }]]]);
    const res = await w.handleXRayFollowup(new Request("https://worker.example/api/xray/followup", {
      method: "POST", headers: { "X-Sync-Key": "k1" }, body: JSON.stringify({ job_id: "j2" })
    }), ENV(db));
    assert.equal(res.status, 200);
    const sent = calls.find(c => c.url.hostname === "api.telegram.org");
    assert.ok(sent.body.text.includes("myhome-ზე ძებნა ვერ მოხერხდა"));
  } finally {
    goOffline();
  }
});

// A MyHome link: the Worker cannot read the listing page either, so a "listing" job reads it.
const LINK = "https://www.myhome.ge/ka/pr/26574188/";
const LISTING_ID = "myhome_26574188";
const savedListing = (extra = {}) => [
  /FROM xray_live_listings WHERE id = \?/,
  () => [{
    id: LISTING_ID, source: "myhome", source_id: "26574188", url: LINK, district: "გლდანი",
    area_m2: 70, floor: "1", rooms: 3, price_usd: 61000, author_id: "4242", user_type: "agent",
    is_owner: 0, phone_number: "568443***", author_listings_count: 24, seen_at: minutesAgo(1), ...extra
  }]
];

test("link X-Ray: an unknown MyHome listing queues a listing job and never fetches MyHome itself", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1();
    const report = await w.handleXRayQuery(LINK, ENV(db), { chatId: CHAT });
    const insert = db.log.find(e => /INSERT INTO xray_jobs/.test(e.sql));
    assert.ok(insert, "no listing job was recorded");
    assert.deepEqual([insert.params[1], insert.params[2], insert.params[3]], ["listing", "", "26574188"]);
    const dispatch = calls.find(c => c.url.hostname === "api.github.com");
    assert.deepEqual(Object.keys(dispatch.body.inputs), ["job_id"]);
    assert.ok(!calls.some(c => /myhome\.ge$|tnet\.ge$/.test(c.url.hostname)), "the Worker still calls MyHome");
    assert.ok(report.includes("🔄 myhome-ზე ვამოწმებ"));
  } finally {
    goOffline();
  }
});

test("link X-Ray: a fresh listing-job row gives the verdict, and no new job starts", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([savedListing()]);
    const report = await w.handleXRayQuery(LINK, ENV(db), { chatId: CHAT });
    assert.ok(report.includes("ფარული მაკლერი"), "24 listings on the account is an agent");
    assert.ok(report.includes("568443***"));
    assert.ok(!report.includes("🔄"));
    assert.ok(!calls.some(c => c.url.hostname === "api.github.com"));
  } finally {
    goOffline();
  }
});

test("link X-Ray: a complete D1 row needs no MyHome check", async () => {
  const calls = serveNetwork();
  try {
    const row = { ...D1_ROW, id: LISTING_ID, source_id: "26574188", url: LINK };
    const db = recordingD1([[/FROM properties WHERE id = \?/, () => [row]]]);
    const report = await w.handleXRayQuery(LINK, ENV(db), { chatId: CHAT });
    assert.ok(!calls.some(c => c.url.hostname === "api.github.com"));
    assert.ok(!report.includes("🔄") && !report.includes("ვერ მივიღეთ"));
  } finally {
    goOffline();
  }
});

test("link X-Ray: without a GitHub token an incomplete D1 row says the status is ours", async () => {
  serveNetwork();
  try {
    const row = { ...D1_ROW, id: LISTING_ID, source_id: "26574188", url: LINK, is_owner: null };
    const db = recordingD1([[/FROM properties WHERE id = \?/, () => [row]]]);
    const report = await w.handleXRayQuery(LINK, { DB: db }, { chatId: CHAT });
    assert.ok(report.includes("myhome-ის შედეგი ახლა ვერ მივიღეთ, სტატუსი ჩვენი ბაზით არის"));
  } finally {
    goOffline();
  }
});

test("follow-up: a finished listing job sends the full link X-Ray", async () => {
  const calls = serveNetwork();
  try {
    const db = recordingD1([
      [/FROM xray_jobs WHERE id = \?/, () => [{ id: "j3", kind: "listing", listing_id: "26574188", chat_id: "777", status: "done" }]],
      savedListing()
    ]);
    const res = await w.handleXRayFollowup(new Request("https://worker.example/api/xray/followup", {
      method: "POST", headers: { "X-Sync-Key": "k1" }, body: JSON.stringify({ job_id: "j3" })
    }), ENV(db));
    assert.equal(res.status, 200);
    const sent = calls.find(c => c.url.hostname === "api.telegram.org");
    assert.equal(sent.body.chat_id, "777");
    assert.ok(sent.body.text.includes("განცხადების რენტგენი"));
    assert.ok(sent.body.text.includes("ფარული მაკლერი"));
    assert.ok(!db.log.some(e => /INSERT INTO xray_jobs/.test(e.sql)), "the follow-up queued another job");
  } finally {
    goOffline();
  }
});
