// After 💰 or 📐 the chat waits for two numbers. A command sent meanwhile must run as a command
// and cancel the wait, instead of the "არასწორი ფორმატი" reply.
import test from "node:test";
import assert from "node:assert/strict";
import { loadWorker } from "./load_worker.mjs";

function setup() {
  const W = loadWorker();
  const calls = [];
  W.fetch = async (url, init) => {
    calls.push({ method: String(url).split("/").pop(), body: JSON.parse(init.body) });
    return new Response(JSON.stringify({ ok: true, result: {} }));
  };
  const store = new Map();
  const kv = {
    get: async (k, t) => (store.has(k) ? (t === "json" ? JSON.parse(store.get(k)) : store.get(k)) : null),
    put: async (k, v) => store.set(k, v),
    list: async () => ({ keys: [] })
  };
  const env = { TELEGRAM_BOT_TOKEN: "t", USERS_KV: kv };
  const send = async update => {
    calls.length = 0;
    await W.__worker.fetch(new Request("https://w/", { method: "POST", body: JSON.stringify(update) }), env, {});
  };
  const message = text => ({ message: { chat: { id: 7 }, text, from: { first_name: "T" } } });
  const press = data => ({ callback_query: { id: "c", data, message: { chat: { id: 7 }, message_id: 1 } } });
  const replies = () => calls.filter(c => c.method === "sendMessage").map(c => c.body.text);
  const state = () => JSON.parse(store.get("USER_7")).state;
  return { send, message, press, replies, state };
}

for (const [button, waiting] of [["menu_price", "awaiting_price"], ["menu_area", "awaiting_area"]]) {
  test(`${button}: /start runs and cancels the wait`, async () => {
    const t = setup();
    await t.send(t.message("/start"));
    await t.send(t.press(button));
    assert.equal(t.state(), waiting);

    await t.send(t.message("/start"));
    assert.match(t.replies()[0], /მოგესალმებით/);
    assert.equal(t.state(), null);
  });

  test(`${button}: a command without its own state reset still cancels the wait`, async () => {
    const t = setup();
    await t.send(t.message("/start"));
    await t.send(t.press(button));

    await t.send(t.message("/watchlist"));
    assert.doesNotMatch(t.replies()[0], /არასწორი ფორმატი/);
    assert.equal(t.state(), null);
  });
}

test("the two numbers still set the price after 💰", async () => {
  const t = setup();
  await t.send(t.message("/start"));
  await t.send(t.press("menu_price"));
  await t.send(t.message("50000 70000"));
  assert.match(t.replies()[0], /ფასის დიაპაზონი განახლდა/);
  assert.equal(t.state(), null);
});

test("plain text that is not two numbers still asks again", async () => {
  const t = setup();
  await t.send(t.message("/start"));
  await t.send(t.press("menu_price"));
  await t.send(t.message("ბევრი"));
  assert.match(t.replies()[0], /არასწორი ფორმატი/);
  assert.equal(t.state(), "awaiting_price");
});
