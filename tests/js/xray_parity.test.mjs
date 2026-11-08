// The two copies of the X-Ray rules must give the same answers (AGENTS.md): this runs
// tests/fixtures/xray_rule_vectors.json against the production cloudflare/worker.js, and
// tests/test_xray_parity.py runs the same vectors against the Python twin core/xray.py.
// When a rule changes, change both copies and add a vector. Run: node --test tests/js/
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadWorker, repoRoot } from "./load_worker.mjs";

const w = loadWorker();
const vectors = JSON.parse(
  fs.readFileSync(path.join(repoRoot, "tests", "fixtures", "xray_rule_vectors.json"), "utf8")
);

function runVector(fn, args) {
  switch (fn) {
    case "matches": return w.workerCandidateMatchesTarget(...args);
    case "layout": return w.compareLayout(...args);
    case "total_floors": return w.compareTotalFloors(...args);
    case "location": return w.compareLocation(...args);
    // Copied into this realm, so that deepStrictEqual does not compare two Array prototypes.
    case "aliases": return [...w.expandDistrictAliases(...args)].sort();
    case "verdict": {
      const v = w.evaluateWorkerBrokerVerdict(...args);
      return { is_broker: v.isBroker, badge: v.badge, reason: v.reason };
    }
    default: throw new Error(`unknown vector function: ${fn}`);
  }
}

test("there are enough vectors, and their names are unique", () => {
  assert.ok(vectors.length >= 25);
  assert.equal(new Set(vectors.map(v => v.name)).size, vectors.length);
});

for (const v of vectors) {
  test(`parity: ${v.name}`, () => {
    assert.deepEqual(runVector(v.fn, v.args), v.expected);
  });
}
