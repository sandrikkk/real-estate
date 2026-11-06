// Executes the production X-Ray code in cloudflare/worker.js (not the Python twin).
// Run: node --test tests/js/
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadWorker, loadGoldenPosts } from "./load_worker.mjs";

const w = loadWorker();

// Correct values out of 100 posts (price: out of the USD-priced posts) on the golden set.
// A ratchet: raise a number when the parser improves, never lower it.
// Keep in sync with GOLDEN_BASELINE in tests/test_xray.py.
const GOLDEN_BASELINE = {
  kind: 93, area_m2: 95, floor: 95, rooms: 98, bedrooms: 88,
  total_floors: 98, district: 70, phone9: 100, price_usd: 26
};

function scoreGolden(parse) {
  const norm = v => (v === undefined || v === null || v === "" ? null : v);
  const same = (a, b) => {
    a = norm(a); b = norm(b);
    if (a === null || b === null) return a === b;
    return typeof b === "number" ? Number(a) === b : String(a) === String(b);
  };
  const correct = {}; const total = {};
  for (const post of loadGoldenPosts()) {
    const e = post.expected || {};
    const r = parse(post.text) || {};
    const pairs = {
      kind: [r.kind, e.xray_kind], area_m2: [r.area_m2, e.area_m2], floor: [r.floor, e.floor],
      rooms: [r.rooms, e.rooms], bedrooms: [r.bedrooms, e.bedrooms],
      total_floors: [r.total_floors, e.total_floors], district: [r.district, e.district],
      phone9: [r.phone9, e.phone9]
    };
    if (e.currency === "USD") pairs.price_usd = [r.price_usd, e.price];
    for (const [k, [got, want]] of Object.entries(pairs)) {
      total[k] = (total[k] || 0) + 1;
      if (same(got, want)) correct[k] = (correct[k] || 0) + 1;
    }
  }
  return { correct, total };
}

test("golden FB posts: parser accuracy never drops below the baseline", () => {
  const { correct, total } = scoreGolden(text => w.parseXRayInput(text));
  console.table(Object.fromEntries(Object.keys(total).map(k => [k, `${correct[k] || 0}/${total[k]}`])));
  for (const [field, min] of Object.entries(GOLDEN_BASELINE)) {
    assert.ok((correct[field] || 0) >= min, `${field}: ${correct[field] || 0} < baseline ${min}`);
  }
});

// Minimal D1 stand-in: routes map an SQL regex to the rows that query returns.
function fakeD1(routes = []) {
  return {
    prepare(sql) {
      let params = [];
      const rows = () => {
        const route = routes.find(([re]) => re.test(sql));
        return route ? route[1](params) : [];
      };
      const stmt = {
        bind(...p) { params = p; return stmt; },
        async all() { return { results: rows() }; },
        async first() { return rows()[0] || null; },
        async run() { return { success: true }; }
      };
      return stmt;
    }
  };
}

// Incident 2026-11-06 (docs/incidents/2026-11-06-xray-false-duplicate.md), FB text reconstructed.
const INCIDENT_FB =
  "იყიდება ქალაქური პროექტის ბინა თემქაზე, 3 მ/რ, 4 კვარტალი. 62 კვ.მ, 1 სართული 8 სართულიანი კორპუსის. " +
  "ერთი საძინებელი, მისაღები და ლოჯი. ფასი 79 000$ ტელ: 599 90 90 01";

const NADZALADEVI_ROW = {
  id: "myhome_26524985", source: "myhome", source_id: "26524985",
  url: "https://www.myhome.ge/ka/pr/26524985/", district: "ნაძალადევი", subdistrict: null,
  street: "ზედაზნის ქ. 6", area_m2: 63, floor: "1", total_floors: 12, rooms: 4, bedrooms: 3,
  price_usd: 92000, is_owner: 1, user_type: "physical", phone_number: "+995 555 00 00 02",
  author_id: "a1", author_listings_count: 2
};

test("incident: Temqa 1-bedroom post does not match a 4-room Nadzaladevi flat", async () => {
  const db = fakeD1([[/WHERE district IN/, () => [NADZALADEVI_ROW]]]);
  const report = await w.handleXRayQuery(INCIDENT_FB, { DB: db });
  assert.ok(report, "X-Ray returned no report");
  assert.ok(!report.includes("26524985"), "the unrelated flat was shown as a duplicate");
  assert.ok(report.includes("დუბლიკატი სხვა პორტალზე არ იძებნება"));
});

test("incident post still finds its real duplicate (same layout, same building height)", async () => {
  const sameFlat = {
    ...NADZALADEVI_ROW, id: "myhome_777", source_id: "777", url: "https://www.myhome.ge/ka/pr/777/",
    subdistrict: "თემქა", street: null, area_m2: 62, floor: "1/8", total_floors: 8, rooms: 2, bedrooms: 1,
    price_usd: 81000
  };
  const db = fakeD1([[/WHERE district IN/, () => [NADZALADEVI_ROW, sameFlat]]]);
  const report = await w.handleXRayQuery(INCIDENT_FB, { DB: db });
  assert.ok(report.includes("myhome.ge/ka/pr/777/"), "the real duplicate was dropped");
  assert.ok(!report.includes("26524985"));
});

test("matcher: area + floor with nothing else known is not enough", () => {
  const target = { district: "თემქა", area_m2: 62, floor: "1", rooms: 0, price_usd: 79000 };
  const cand = { district: "ნაძალადევი", area_m2: 62, floor: "1", rooms: null, price_usd: 79000 };
  assert.equal(w.workerCandidateMatchesTarget(target, cand), false);
});

test("matcher: known layout or building-height conflicts reject", () => {
  const base = { district: "საბურთალო", area_m2: 60, floor: "7" };
  // 1 bedroom vs 4 rooms
  assert.equal(w.workerCandidateMatchesTarget({ ...base, bedrooms: 1 }, { ...base, rooms: 4 }), false);
  // rooms agree but 8-storey vs 12-storey building
  assert.equal(
    w.workerCandidateMatchesTarget({ ...base, rooms: 3, total_floors: 8 }, { ...base, rooms: 3, floor: "7/12" }),
    false
  );
});

test("matcher: corroborated duplicates still match", () => {
  const base = { area_m2: 60, floor: "7" };
  // rooms agree across parent/child district (tests/test_xray.py: Nutsubidze vs Saburtalo)
  assert.equal(
    w.workerCandidateMatchesTarget({ ...base, district: "ნუცუბიძე", rooms: 3 }, { ...base, district: "საბურთალო", rooms: 3 }),
    true
  );
  // 2 bedrooms is compatible with 3 rooms
  assert.equal(
    w.workerCandidateMatchesTarget({ ...base, district: "საბურთალო", bedrooms: 2 }, { ...base, district: "საბურთალო", rooms: 3 }),
    true
  );
  // only the child district in the candidate's subdistrict corroborates
  assert.equal(
    w.workerCandidateMatchesTarget(
      { ...base, district: "თემქა" },
      { ...base, district: "ნაძალადევი", subdistrict: "თემქა" }
    ),
    true
  );
  // building height within one floor is not a conflict
  assert.equal(
    w.workerCandidateMatchesTarget({ ...base, district: "საბურთალო", rooms: 3, total_floors: 12 }, { ...base, district: "საბურთალო", rooms: 3, total_floors: 13 }),
    true
  );
});

test("parser reads bedrooms and building height", () => {
  const cases = [
    ["2 საძინებელი, 65 კვ.მ", { bedrooms: 2 }],
    ["ორი ნათელი საძინებლით, 65 კვ.მ", { bedrooms: 2 }],
    ["ერთი საძინებლით, 45 კვ.მ", { bedrooms: 1 }],
    ["65 კვ.მ, სართული: 7/25", { floor: "7", total_floors: 25 }],
    ["65 კვ.მ, 1-ლი სართული 5-სართულიან კორპუსში", { floor: "1", total_floors: 5 }],
    ["65 კვ.მ, 2-ე სართული (9) სართულიანია", { floor: "2", total_floors: 9 }],
    ["65 კვ.მ, 8-სართულიანი კორპუსის მე-6 სართული", { floor: "6", total_floors: 8 }]
  ];
  for (const [text, want] of cases) {
    const got = w.parseXRayInput(`იყიდება ბინა საბურთალოზე ${text}`);
    for (const [k, v] of Object.entries(want)) assert.equal(got?.[k], v, `${k} in "${text}"`);
  }
});

test("owner signal: empty broker/agency objects are not agent evidence", () => {
  for (const empty of [{}, [], { id: null }, { name: "" }]) {
    const d = w.parseMyHomeStatementDetails({ is_owner: true, user_type: { type: "physical" }, user_statements_count: 2, broker: empty, agency: empty }, "1", "");
    assert.equal(d.is_owner, 1, `broker=${JSON.stringify(empty)}`);
  }
  const real = w.parseMyHomeStatementDetails({ is_owner: true, user_statements_count: 1, agency: { id: 55, name: "X" } }, "1", "");
  assert.equal(real.is_owner, 0);
});

test("merge: a live portal answer replaces the stored one, and a full phone replaces a masked one", () => {
  const healed = w.mergeLiveIntoRow(
    { is_owner: 0, user_type: "agent", phone_number: "555000***" },
    { is_owner: 1, user_type: "physical", phone_number: "+995 555 00 00 02" }
  );
  assert.equal(healed.is_owner, 1);
  assert.equal(healed.user_type, "physical");
  assert.equal(healed.phone_number, "+995 555 00 00 02");

  const agent = w.mergeLiveIntoRow({ is_owner: 1, user_type: "physical" }, { is_owner: 0, user_type: "agent" });
  assert.equal(agent.is_owner, 0);
  assert.equal(agent.user_type, "agent");

  const unknown = w.mergeLiveIntoRow({ is_owner: 0, phone_number: "+995 555 00 00 02" }, { is_owner: null, phone_number: "555000***" });
  assert.equal(unknown.is_owner, 0);
  assert.equal(unknown.phone_number, "+995 555 00 00 02");
});

test("enrichment re-fetches a duplicate whose stored phone is masked", async () => {
  let calls = 0;
  w.fetch = async () => { calls++; return new Response("offline", { status: 503 }); };
  try {
    const dup = { id: "myhome_1", source: "myhome", source_id: "1", url: "https://www.myhome.ge/ka/pr/1/", phone_number: "555000***", is_owner: 0, author_id: "a" };
    await w.enrichDuplicatesIfNeeded(fakeD1(), [dup], 3);
    assert.ok(calls > 0, "masked phone was treated as complete");
  } finally {
    w.fetch = async () => new Response("offline", { status: 503 });
  }
});

test("webhook secret: wrong secret is 401, right secret is accepted", async () => {
  assert.equal(typeof w.timingSafeEqualStr, "function");
  assert.equal(w.timingSafeEqualStr("abc", "abc"), true);
  assert.equal(w.timingSafeEqualStr("abc", "abx"), false);
  assert.equal(w.timingSafeEqualStr("abc", "abcd"), false);

  const env = { TELEGRAM_WEBHOOK_SECRET: "s3cret", TELEGRAM_BOT_TOKEN: "t", USERS_KV: { get: async () => null, put: async () => {} } };
  const post = secret => new Request("https://worker.example/telegram", {
    method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": secret }, body: "{}"
  });
  assert.equal((await w.handleTelegramWebhook(post("wrong"), env)).status, 401);
  assert.equal((await w.handleTelegramWebhook(post("s3cret"), env)).status, 200);
});

test("sync key uses the same constant-time compare", () => {
  const req = key => new Request("https://worker.example/api/users", { headers: { "X-Sync-Key": key } });
  assert.equal(w.isSyncKeyValid(req("k1"), { SYNC_KEY: "k1" }), true);
  assert.equal(w.isSyncKeyValid(req("k2"), { SYNC_KEY: "k1" }), false);
  assert.equal(w.isSyncKeyValid(req(""), { SYNC_KEY: "" }), false);
});

test("a photo post's caption is handled like message text", async () => {
  const gets = [];
  const env = { TELEGRAM_BOT_TOKEN: "t", USERS_KV: { get: async k => { gets.push(k); return null; }, put: async () => {} } };
  const update = { message: { chat: { id: 123 }, caption: INCIDENT_FB, photo: [{ file_id: "x" }] } };
  const req = new Request("https://worker.example/telegram", { method: "POST", body: JSON.stringify(update) });
  await w.handleTelegramWebhook(req, env);
  assert.ok(gets.includes("USER_123"), "caption message never reached handleMessage");
});
