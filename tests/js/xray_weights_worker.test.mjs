// ADR 0002: worker exact duplicates use the newest passed weights from D1 (PR 8).
// Runs cloudflare/worker.js offline with fake D1 and Telegram.
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadWorker, repoRoot } from "./load_worker.mjs";

const w = loadWorker();
const sampleWeights = JSON.parse(
  fs.readFileSync(path.join(repoRoot, "tests", "fixtures", "xray_weights_sample.json"), "utf8")
);
const sampleWeightsV2 = JSON.parse(
  fs.readFileSync(path.join(repoRoot, "tests", "fixtures", "xray_weights_sample_v2.json"), "utf8")
);

const CHAT = 123;
const PHONE = "555000111";
const POST =
  "იყიდება 3-ოთახიანი ბინა გლდანში, მე-6 მიკრორაიონი, 72 მ², 1 სართული, $60,000. დარეკეთ: " + PHONE;

const DUP_AGREE = {
  id: "ss_ge_1", source: "ss_ge", url: "https://home.ss.ge/ka/udzravi-qoneba/1",
  district: "გლდანი", street: "გლდანი - VI მ/რ", area_m2: 72, floor: "1", rooms: 3, price_usd: 60000,
  is_owner: true, phone_number: "599111222"
};
const DUP_ONLY_FA = {
  id: "ss_ge_2", source: "ss_ge", url: "https://home.ss.ge/ka/udzravi-qoneba/2",
  district: "გლდანი", street: "", area_m2: 72, floor: "1", rooms: null, price_usd: 60000,
  is_owner: true, phone_number: "599111333"
};

const passedWeightsRoute = [
  /FROM xray_weights WHERE status = 'passed'/,
  () => [{ id: "w-2026-W46", weights: JSON.stringify(sampleWeights) }]
];
const dupRoute = [
  /FROM properties WHERE district IN/,
  () => [{ ...DUP_AGREE }, { ...DUP_ONLY_FA }]
];

function recordingD1(routes = []) {
  const log = [];
  return {
    log,
    prepare(sql) {
      let params = [];
      const getHandler = () => {
        const route = routes.find(([re]) => re.test(sql));
        return route ? route[1] : null;
      };
      const stmt = {
        bind(...p) { params = p; return stmt; },
        async all() {
          log.push({ sql, params });
          const h = getHandler();
          if (typeof h === "function") {
            const res = h(params);
            return { results: Array.isArray(res) ? res : [] };
          }
          return { results: [] };
        },
        async first() {
          log.push({ sql, params });
          const h = getHandler();
          if (typeof h === "function") {
            const res = h(params);
            return Array.isArray(res) ? res[0] || null : res;
          }
          return null;
        },
        async run() {
          log.push({ sql, params });
          const h = getHandler();
          if (typeof h === "function") h(params);
          return { success: true };
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
const message = text => ({ message: { chat: { id: CHAT }, from: { id: CHAT }, text } });

await test("1. weights from D1: shows agreeing candidate with confidence, rejects floor-and-area alone", async () => {
  w.resetXRayWeightsCache();
  const calls = serveNetwork();
  const db = recordingD1([passedWeightsRoute, dupRoute]);
  await webhook(env(db), message(POST));
  const reply = telegram(calls, "sendMessage").find(c => c.body.text.includes("რენტგენი"));
  assert.ok(reply, "reply sent");
  assert.ok(reply.body.text.includes("სავარაუდოდ 86%"), "includes confidence percent");
  assert.ok(reply.body.text.includes(DUP_AGREE.url), "includes agreeing duplicate");
  assert.ok(!reply.body.text.includes(DUP_ONLY_FA.url), "rejects floor-and-area alone");
});

await test("2. no passed row: fallback to §5.5 rule without confidence percentage", async () => {
  w.resetXRayWeightsCache();
  const calls = serveNetwork();
  const noPassedRoute = [
    /FROM xray_weights WHERE status = 'passed'/,
    () => []
  ];
  const db = recordingD1([noPassedRoute, dupRoute]);
  await webhook(env(db), message(POST));
  const reply = telegram(calls, "sendMessage").find(c => c.body.text.includes("რენტგენი"));
  assert.ok(reply, "reply sent");
  assert.ok(reply.body.text.includes(DUP_AGREE.url), "includes agreeing duplicate under old rule");
  assert.ok(!reply.body.text.includes("სავარაუდოდ"), "no confidence percentage");
});

await test("3. invalid weights: fallback to §5.5 rule without confidence percentage", async () => {
  w.resetXRayWeightsCache();
  const calls = serveNetwork();
  const invalidWeightsRoute = [
    /FROM xray_weights WHERE status = 'passed'/,
    () => [{ id: "w-bad", weights: JSON.stringify({ levels_version: 3 }) }]
  ];
  const db = recordingD1([invalidWeightsRoute, dupRoute]);
  await webhook(env(db), message(POST));
  const reply = telegram(calls, "sendMessage").find(c => c.body.text.includes("რენტგენი"));
  assert.ok(reply, "reply sent");
  assert.ok(reply.body.text.includes(DUP_AGREE.url), "includes agreeing duplicate");
  assert.ok(!reply.body.text.includes("სავარაუდოდ"), "no confidence percentage");
});

await test("4. D1 error on weights query: old rule decides and next request retries query", async () => {
  w.resetXRayWeightsCache();
  const calls = serveNetwork();
  let queryCount = 0;
  const errorWeightsRoute = [
    /FROM xray_weights WHERE status = 'passed'/,
    () => {
      queryCount++;
      throw new Error("D1 connection lost");
    }
  ];
  const db = recordingD1([errorWeightsRoute, dupRoute]);
  await webhook(env(db), message(POST));
  const reply1 = telegram(calls, "sendMessage").find(c => c.body.text.includes("რენტგენი"));
  assert.ok(reply1, "reply 1 sent");
  assert.ok(!reply1.body.text.includes("სავარაუდოდ"), "no confidence on error");
  assert.equal(queryCount, 1, "first query attempted");

  // Second request: should retry querying xray_weights
  await webhook(env(db), message(POST));
  assert.equal(queryCount, 2, "second query retried (not cached on error)");
});

await test("5. loaded once per isolate: two requests query xray_weights only once", async () => {
  w.resetXRayWeightsCache();
  const calls = serveNetwork();
  const db = recordingD1([passedWeightsRoute, dupRoute]);
  await webhook(env(db), message(POST));
  await webhook(env(db), message(POST));
  const weightsQueries = db.log.filter(e => /FROM xray_weights/.test(e.sql));
  assert.equal(weightsQueries.length, 1, "only 1 query to xray_weights across 2 requests");
});

await test("6. photo group: candidate shows ' · იგივე ფოტოები' even when text scorer would reject it", async () => {
  w.resetXRayWeightsCache();
  const calls = serveNetwork();
  const targetWithGroup = {
    id: "myhome_26574188", source: "myhome", url: "https://www.myhome.ge/ka/pr/26574188/",
    district: "გლდანი", area_m2: 72, floor: "1", rooms: 3, price_usd: 60000,
    group_id: "photo_grp_100", is_owner: true, phone_number: "555000111", author_id: "auth1", author_listings_count: 1
  };
  const photoGroupCandidate = {
    id: "ss_ge_99", source: "ss_ge", url: "https://home.ss.ge/ka/udzravi-qoneba/99",
    district: "საბურთალო", area_m2: 120, floor: "8", rooms: 4, price_usd: 95000,
    group_id: "photo_grp_100", is_owner: true, phone_number: "599222333"
  };
  const propertyLookupRoute = [
    /FROM properties WHERE id = \?/,
    () => [targetWithGroup]
  ];
  const groupRoute = [
    /FROM properties WHERE group_id = \? AND id != \?/,
    () => [photoGroupCandidate]
  ];
  const db = recordingD1([passedWeightsRoute, propertyLookupRoute, groupRoute, dupRoute]);
  await webhook(env(db), message("https://www.myhome.ge/ka/pr/26574188/"));
  const reply = telegram(calls, "sendMessage").find(c => c.body.text.includes("რენტგენი"));
  assert.ok(reply, "reply sent");
  assert.ok(reply.body.text.includes(" · იგივე ფოტოები"), "shows ' · იგივე ფოტოები' for photo group");
  assert.ok(reply.body.text.includes(photoGroupCandidate.url), "includes photo group duplicate");
});

await test("7. saved snapshot carries xray_posterior and xray_weights", async () => {
  w.resetXRayWeightsCache();
  const calls = serveNetwork();
  const db = recordingD1([passedWeightsRoute, dupRoute]);
  await webhook(env(db), message(POST));
  const insertLog = db.log.find(e => /INSERT INTO xray_results/.test(e.sql));
  assert.ok(insertLog, "INSERT INTO xray_results logged");
  const candidatesJson = insertLog.params[4];
  const candidates = JSON.parse(candidatesJson);
  assert.ok(candidates.length > 0, "has candidates");
  assert.equal(candidates[0].xray_weights, "w-2026-W46");
  assert.equal(typeof candidates[0].xray_posterior, "number");
  assert.equal(Math.round(candidates[0].xray_posterior * 100), 86);
});

await test("8. v2 weights from D1: loads v2 row and shows 76% on 9.0 pair (lower than v1)", async () => {
  w.resetXRayWeightsCache();
  const calls = serveNetwork();
  const passedV2Route = [
    /FROM xray_weights WHERE status = 'passed'/,
    () => [{ id: sampleWeightsV2.id, weights: JSON.stringify(sampleWeightsV2) }]
  ];
  const target9 = { district: "გლდანი", rooms: 3, bedrooms: 2, floor: "8", total_floors: 10, area_m2: 50 };
  const cand9 = {
    id: "ss_ge_9", source: "ss_ge", url: "https://home.ss.ge/ka/udzravi-qoneba/9",
    district: "გლდანი", street: "შენგელია რ. ქ. 7", rooms: 3, bedrooms: 2, floor: "8", total_floors: 9, area_m2: 50, price_usd: 60000,
    is_owner: true, phone_number: "599111444"
  };
  const dup9Route = [
    /FROM properties WHERE district IN/,
    () => [cand9]
  ];
  const post9 = "იყიდება 3-ოთახიანი ბინა გლდანში, 50 მ², 8/10 სართული, $60,000. დარეკეთ: " + PHONE;
  const db = recordingD1([passedV2Route, dup9Route]);
  await webhook(env(db), message(post9));
  const reply = telegram(calls, "sendMessage").find(c => c.body.text.includes("რენტგენი"));
  assert.ok(reply, "reply sent");
  assert.ok(reply.body.text.includes("სავარაუდოდ 76%"), "includes v2 confidence percent 76%");
  assert.ok(reply.body.text.includes(cand9.url), "includes duplicate");

  const p1 = w.workerChallengerPosterior(sampleWeights, target9, cand9);
  const p2 = w.workerChallengerPosterior(sampleWeightsV2, target9, cand9);
  assert.equal(Math.round((p1.shown || p1) * 1e6) / 1e6, 0.853746);
  assert.equal(Math.round((p2.shown || p2) * 1e6) / 1e6, 0.762542);
  assert.ok((p2.shown || p2) < (p1.shown || p1), "v2 posterior is lower than v1");
});

await test("9. with calibration row: pair at raw 0.854 shows 62%, pair at raw 0.68 is not shown", async () => {
  w.resetXRayWeightsCache();
  const calls = serveNetwork();
  const calData = {
    breakpoints: [
      [0.70, 0.44],
      [0.90, 0.62],
      [0.99, 0.94]
    ],
    answers: 65
  };
  const passedWeightsRoute = [
    /FROM xray_weights WHERE status = 'passed'/,
    () => [{ id: "w-2026-W46", weights: JSON.stringify(sampleWeights) }]
  ];
  const calRoute = [
    /FROM xray_calibration WHERE weights_id = \?/,
    () => [{ calibration: JSON.stringify(calData) }]
  ];
  const cand1 = { ...DUP_AGREE, id: "ss_ge_cand1", url: "https://home.ss.ge/ka/udzravi-qoneba/cand1" };
  const cand2 = {
    id: "ss_ge_cand2", source: "ss_ge", url: "https://home.ss.ge/ka/udzravi-qoneba/cand2",
    district: "გლდანი", street: "გლდანი - VII მ/რ", area_m2: 72, floor: "1", rooms: 3, price_usd: 60000,
    is_owner: true, phone_number: "599111888"
  };
  const dupMultiRoute = [
    /FROM properties WHERE district IN/,
    () => [cand1, cand2]
  ];

  const db = recordingD1([passedWeightsRoute, calRoute, dupMultiRoute]);
  await webhook(env(db), message(POST));
  const reply = telegram(calls, "sendMessage").find(c => c.body.text.includes("რენტგენი"));
  assert.ok(reply, "reply sent");
  assert.ok(reply.body.text.includes("სავარაუდოდ 62%"), "cand1 calibrated to 62%");
  assert.ok(reply.body.text.includes(cand1.url), "includes cand1");
  assert.ok(!reply.body.text.includes(cand2.url), "rejects cand2 because calibrated rate 44% <= 50%");
});

await test("10. invalid calibration: ignored and falls back to raw weights", async () => {
  w.resetXRayWeightsCache();
  const calls = serveNetwork();
  const invalidCalData = {
    breakpoints: [
      [0.70, 0.44],
      [0.50, 0.62]
    ],
    answers: 65
  };
  const passedWeightsRoute = [
    /FROM xray_weights WHERE status = 'passed'/,
    () => [{ id: "w-2026-W46", weights: JSON.stringify(sampleWeights) }]
  ];
  const calRoute = [
    /FROM xray_calibration WHERE weights_id = \?/,
    () => [{ calibration: JSON.stringify(invalidCalData) }]
  ];
  const cand1 = { ...DUP_AGREE, id: "ss_ge_cand1" };
  const dupRoute = [
    /FROM properties WHERE district IN/,
    () => [cand1]
  ];
  const db = recordingD1([passedWeightsRoute, calRoute, dupRoute]);
  await webhook(env(db), message(POST));
  const reply = telegram(calls, "sendMessage").find(c => c.body.text.includes("რენტგენი"));
  assert.ok(reply, "reply sent");
  assert.ok(reply.body.text.includes("სავარაუდოდ 86%"), "fallback to raw posterior (86%)");
});
