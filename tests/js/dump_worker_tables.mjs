// Prints the Worker's district tables as JSON, for tests/test_xray_parity.py.
import vm from "node:vm";
import { loadWorker } from "./load_worker.mjs";

const w = loadWorker();
process.stdout.write(
  vm.runInContext("JSON.stringify({ keywords: XRAY_DISTRICT_KEYWORDS, aliases: XRAY_DISTRICT_ALIASES })", w)
);
