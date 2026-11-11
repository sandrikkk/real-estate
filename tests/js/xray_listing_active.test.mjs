// Tests for phone report handling of dead/expired listings (SS.ge status and live MyHome).
// Runs the production cloudflare/worker.js offline: D1 and SS.ge API are fakes.
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadWorker } from "./load_worker.mjs";

const w = loadWorker();
const goOffline = () => {
  w.fetch = async () => new Response("offline", { status: 503 });
};

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

function serveSsGe(statusById) {
  w.fetch = async (url) => {
    const u = new URL(String(url));
    if (u.hostname === "home.ss.ge" && u.pathname === "/api/refresh_access_token") {
      return new Response("{}", { status: 200, headers: { "x-ss-session-token": "tok" } });
    }
    if (u.hostname === "api-gateway.ss.ge") {
      const id = u.searchParams.get("applicationId");
      const status = statusById[id];
      if (!status) return new Response("{}", { status: 400 });
      return new Response(JSON.stringify({
        applicationId: Number(id), status, isInactiveApplication: status !== "Active",
        userEntityType: "Broker"
      }), { status: 200 });
    }
    return new Response("offline", { status: 503 });
  };
}

const flat = { district: "გლდანი", area_m2: 72, floor: "1", rooms: 3 };
const REF = {
  ...flat, id: "ss_ge_100", source: "ss_ge", source_id: "100",
  url: "https://home.ss.ge/ka/udzravi-qoneba/100", street: "გლდანი - VII მ/რ",
  price_usd: 61000, is_owner: false, phone_number: "599111222"
};
const DUP_A = {
  ...flat, id: "ss_ge_201", source: "ss_ge", source_id: "201",
  url: "https://home.ss.ge/ka/udzravi-qoneba/201", street: "გლდანი - VII მ/რ 12",
  price_usd: 60000
};
const DUP_B = {
  ...DUP_A, id: "ss_ge_202", source_id: "202",
  url: "https://home.ss.ge/ka/udzravi-qoneba/202", price_usd: 62000
};
const MASKED_MYHOME = {
  ...flat, id: "myhome_900", source: "myhome",
  url: "https://www.myhome.ge/ka/pr/900/", price_usd: 545, phone_number: "599111***"
};
const parsed = { formattedPhone: "599 11 12 22", phone9: "599111222", maskedPhone: "599111***" };
const phoneRoute = rows => [/FROM properties WHERE phone_number IN/, () => rows];
const dupRoute = rows => [/FROM properties WHERE district IN/, () => rows];
const FRESH = { mode: "fresh", full: false, rows: [] };

test("SS.ge status maps to is_active", () => {
  assert.equal(
    w.parseSsGeStatementDetails({ isInactiveApplication: true, status: "Expired" }, {}, "", "1", "").is_active,
    false
  );
  assert.equal(
    w.parseSsGeStatementDetails({ isInactiveApplication: false, status: "Active" }, {}, "", "1", "").is_active,
    true
  );
  assert.equal(
    w.parseSsGeStatementDetails({}, {}, "", "1", "").is_active,
    null
  );
});

test("an expired duplicate is not shown", async () => {
  serveSsGe({ 100: "Active", 201: "Expired", 202: "Active" });
  try {
    const db = recordingD1([phoneRoute([{ ...REF }]), dupRoute([{ ...DUP_A }, { ...DUP_B }])]);
    const collect = {};
    const text = await w.buildPhoneReport(db, parsed, { ...FRESH }, null, collect);
    assert.ok(text.includes(DUP_B.url));
    assert.ok(!text.includes(DUP_A.url));
    assert.equal(collect.duplicates?.length, 1);
    assert.equal(collect.duplicates[0].id, "ss_ge_202");
  } finally {
    goOffline();
  }
});

test("no section when every duplicate is expired", async () => {
  serveSsGe({ 100: "Active", 201: "Expired", 202: "Expired" });
  try {
    const db = recordingD1([phoneRoute([{ ...REF }]), dupRoute([{ ...DUP_A }, { ...DUP_B }])]);
    const collect = {};
    const text = await w.buildPhoneReport(db, parsed, { ...FRESH }, null, collect);
    assert.ok(!text.includes("ამ ნომრის სხვა ბინები სხვაგანაც"));
    assert.equal(collect.duplicates, undefined);
  } finally {
    goOffline();
  }
});

test("an expired listing of the number is marked and gets no pairs", async () => {
  serveSsGe({ 100: "Expired", 201: "Active", 202: "Active" });
  try {
    const db = recordingD1([phoneRoute([{ ...REF }]), dupRoute([{ ...DUP_A }, { ...DUP_B }])]);
    const collect = {};
    const text = await w.buildPhoneReport(db, parsed, { ...FRESH }, null, collect);
    assert.ok(text.includes("🚫 აღარ არის აქტიური"));
    assert.ok(!text.includes("ამ ნომრის სხვა ბინები სხვაგანაც"));
    assert.ok(!db.log.some(e => /FROM properties WHERE district IN/.test(e.sql)));
  } finally {
    goOffline();
  }
});

test("a complete live answer drops stored MyHome rows", async () => {
  try {
    const db = recordingD1([phoneRoute([{ ...MASKED_MYHOME }])]);
    const text = await w.buildPhoneReport(db, parsed, { ...FRESH });
    assert.ok(text.includes("ვერ მოიძებნა ვერც myhome-ზე და ვერც ჩვენს ბაზაში"));
  } finally {
    goOffline();
  }
});

test("stored MyHome rows stay while the lookup is running", async () => {
  try {
    const db = recordingD1([phoneRoute([{ ...MASKED_MYHOME }])]);
    const text = await w.buildPhoneReport(db, parsed, { mode: "queued", rows: [] });
    assert.ok(text.includes(MASKED_MYHOME.url));
  } finally {
    goOffline();
  }
});

test("stored MyHome rows stay when the live page was full", async () => {
  try {
    const row = { ...MASKED_MYHOME, phone_number: "599111222" };
    const live = {
      mode: "fresh",
      full: true,
      rows: [{ ...flat, id: "myhome_901", source: "myhome", url: "https://www.myhome.ge/ka/pr/901/", price_usd: 700 }]
    };
    const db = recordingD1([phoneRoute([row])]);
    const text = await w.buildPhoneReport(db, parsed, live);
    assert.ok(text.includes("https://www.myhome.ge/ka/pr/900/"));
  } finally {
    goOffline();
  }
});

test("an unreachable SS.ge keeps the duplicates", async () => {
  goOffline();
  try {
    const db = recordingD1([phoneRoute([{ ...REF }]), dupRoute([{ ...DUP_A }, { ...DUP_B }])]);
    const collect = {};
    const text = await w.buildPhoneReport(db, parsed, { ...FRESH }, null, collect);
    assert.ok(text.includes(DUP_A.url));
    assert.ok(text.includes(DUP_B.url));
  } finally {
    goOffline();
  }
});
