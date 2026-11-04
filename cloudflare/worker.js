/**
 * Cloudflare Worker: 24/7 Serverless Telegram Bot UI + KV Storage + Proxy Engine
 *
 * Capabilities:
 * 1. Telegram Webhook: Serves /start, /filter, inline keyboard buttons for districts, prices, area.
 * 2. Cloudflare KV Database: Stores user subscriptions and preferences at the edge.
 * 3. Protected Sync API: GET /api/users for Python runner to fetch all active user filters.
 * 4. Scraper Proxy: GET /?url=... maintains existing Cloudflare proxy functionality for MyHome/SS.ge.
 */

const DEFAULT_DISTRICTS = [
  "დიდუბე",
  "ნაძალადევი",
  "ჩუღურეთი",
  "ისანი",
  "გლდანი",
  "საბურთალო",
  "ვაკე",
  "სამგორი",
  "მთაწმინდა",
  "კრწანისი",
  "დიღმის მასივი",
  "დიდი დიღომი"
];

const DEFAULT_USER_PROFILE = {
  deal_type: "sale",
  price_min_usd: 10000,
  price_max_usd: 100000,
  rent_price_min_usd: 300,
  rent_price_max_usd: 1500,
  area_min_m2: 10,
  area_max_m2: 100,
  rooms_min: 2,
  districts: [
    "დიდუბე",
    "ნაძალადევი",
    "ჩუღურეთი",
    "ისანი",
    "გლდანი",
    "ვაკე",
    "სამგორი",
    "მთაწმინდა",
    "კრწანისი",
    "დიღმის მასივი"
  ],
  owner_type: "all",
  is_active: true,
  state: null
};

// In-memory fallback if KV namespace is not yet bound (for local testing/dry run)
const memoryStore = new Map();

async function getKV(env) {
  let kvObj = null;

  // 1. Direct or trimmed env check
  if (env) {
    if (env.USERS_KV && typeof env.USERS_KV.get === "function") {
      kvObj = env.USERS_KV;
    } else {
      for (const key of Object.keys(env)) {
        if (key.trim() === "USERS_KV" && typeof env[key]?.get === "function") {
          kvObj = env[key];
          break;
        }
      }
    }
  }

  // 2. Search all properties on env for ANY KV namespace binding
  if (!kvObj && env) {
    for (const key of Object.keys(env)) {
      const val = env[key];
      if (val && typeof val === "object" && typeof val.get === "function" && typeof val.put === "function") {
        kvObj = val;
        break;
      }
    }
  }

  // 3. Search globalThis
  if (!kvObj && typeof globalThis !== "undefined") {
    if (globalThis.USERS_KV && typeof globalThis.USERS_KV.get === "function") {
      kvObj = globalThis.USERS_KV;
    } else {
      for (const key of Object.getOwnPropertyNames(globalThis)) {
        try {
          const val = globalThis[key];
          if (val && typeof val === "object" && typeof val.get === "function" && typeof val.put === "function") {
            kvObj = val;
            break;
          }
        } catch (e) {}
      }
    }
  }

  if (kvObj) {
    return {
      get: async (key, opt) => {
        const type = typeof opt === "string" ? opt : (opt?.type || "text");
        try {
          const raw = await kvObj.get(key, "text");
          if (!raw) return null;
          if (type === "json") {
            try {
              return JSON.parse(raw);
            } catch (e) {
              return null;
            }
          }
          return raw;
        } catch (err) {
          return null;
        }
      },
      put: async (key, val) => {
        try {
          const strVal = typeof val === "string" ? val : JSON.stringify(val);
          return await kvObj.put(key, strVal);
        } catch (err) {
          return null;
        }
      },
      list: async (opt) => {
        try {
          return await kvObj.list(opt);
        } catch (err) {
          return { keys: [] };
        }
      }
    };
  }

  // Fallback to in-memory store
  return {
    get: async (key, opt) => {
      const type = typeof opt === "string" ? opt : (opt?.type || "text");
      const val = memoryStore.get(key);
      if (!val) return null;
      if (type === "json") {
        return typeof val === "string" ? JSON.parse(val) : val;
      }
      return val;
    },
    put: async (key, val) => {
      memoryStore.set(key, typeof val === "string" ? val : JSON.stringify(val));
    },
    list: async ({ prefix }) => {
      const keys = Array.from(memoryStore.keys())
        .filter(k => k.startsWith(prefix))
        .map(name => ({ name }));
      return { keys };
    }
  };
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. Scraper Proxy Route (Backward Compatibility for MyHome/SS.ge)
    const targetUrl = url.searchParams.get("url");
    if (targetUrl && ["GET", "POST", "PUT"].includes(request.method)) {
      return handleProxy(targetUrl, request);
    }

    // 2. Protected Sync API for Python Runner: GET /api/users
    if (url.pathname === "/api/users") {
      return handleApiUsers(request, env);
    }

    // 2.1 Protected D1 Database API for Python Runner: POST /api/db/query
    if (url.pathname === "/api/db/query" && request.method === "POST") {
      return handleDbRequest(request, env);
    }

    // Debug status endpoint
    if (url.pathname === "/api/debug") {
      const kv = await getKV(env);
      let listKeys = [];
      let rawSample = null;
      try {
        const l = await kv.list({ prefix: "" });
        listKeys = l.keys || [];
        if (listKeys.length > 0) {
          rawSample = await kv.get(listKeys[0].name, "text");
        }
      } catch (e) {
        listKeys = [e.message];
      }
      return new Response(JSON.stringify({
        envKeys: Object.keys(env || {}),
        envTypes: Object.fromEntries(Object.keys(env || {}).map(k => [k, typeof env[k]])),
        foundKVInEnv: !!(env && Object.values(env).some(v => v && typeof v === "object" && typeof v.get === "function")),
        allKeys: listKeys,
        sampleData: rawSample
      }, null, 2), { headers: { "content-type": "application/json" } });
    }

    // 3. Telegram Webhook Endpoint: POST / or POST /telegram-webhook
    if (request.method === "POST") {
      return handleTelegramWebhook(request, env);
    }

    // 4. Default Health Check / Info
    return new Response(
      JSON.stringify({
        status: "online",
        service: "Tbilisi Real Estate Cloudflare Service",
        time: new Date().toISOString(),
        endpoints: {
          webhook: "POST /telegram-webhook",
          api: "GET /api/users",
          proxy: "GET /?url=<target>"
        }
      }, null, 2),
      { headers: { "content-type": "application/json; charset=utf-8" } }
    );
  }
};

/**
 * Handles proxying HTML requests (bypasses Cloudflare on target sites)
 */
async function handleProxy(targetUrl, request) {
  try {
    const parsed = new URL(targetUrl);
    const headers = new Headers();
    headers.set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36");
    headers.set("Accept-Language", "ka-GE,ka;q=0.9,en-US;q=0.8,en;q=0.7");

    // Detect tnet.ge JSON API requests and use appropriate headers
    if (parsed.hostname.includes("tnet.ge") || parsed.hostname.includes("api-statements")) {
      headers.set("Accept", "application/json, text/plain, */*");
      headers.set("locale", "ka");
      headers.set("X-Website-Key", "myhome");
    } else if (parsed.hostname.includes("api-gateway.ss.ge") || parsed.pathname.includes("/api/refresh_access_token")) {
      headers.set("Accept", "application/json, text/plain, */*");
      headers.set("Accept-Language", "ka");
      headers.set("os", "web");
      headers.set("Origin", "https://home.ss.ge");
      headers.set("Referer", "https://home.ss.ge/");
      const authHeader = request.headers.get("Authorization") || request.headers.get("authorization");
      if (authHeader) headers.set("Authorization", authHeader);
      const contentType = request.headers.get("Content-Type") || request.headers.get("content-type");
      if (contentType) headers.set("Content-Type", contentType);
    } else {
      headers.set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8");
    }

    const method = request.method || "GET";
    const fetchInit = { method, headers };
    if (method !== "GET" && method !== "HEAD") {
      fetchInit.body = await request.arrayBuffer();
    }

    const response = await fetch(parsed.toString(), fetchInit);
    const outHeaders = {
      "content-type": response.headers.get("content-type") || "text/html; charset=utf-8",
      "Access-Control-Allow-Origin": "*"
    };
    const setCookie = response.headers.get("set-cookie") || "";
    const tokenMatch = setCookie.match(/ss-session-token=([^;\s,]+)/);
    if (tokenMatch) {
      outHeaders["x-ss-session-token"] = tokenMatch[1];
    }

    return new Response(response.body, {
      status: response.status,
      headers: outHeaders
    });
  } catch (err) {
    return new Response(`Proxy Error: ${err.message}`, { status: 502 });
  }
}

/**
 * Returns JSON list of active users to the Python Runner
 */
async function handleApiUsers(request, env) {
  try {
    const expectedKey = (env.SYNC_KEY || "").trim();
    if (expectedKey) {
      const syncKey = (request.headers.get("X-Sync-Key") || new URL(request.url).searchParams.get("key") || "").trim();
      if (syncKey !== expectedKey) {
        return new Response(JSON.stringify({ error: "Unauthorized" }), {
          status: 401,
          headers: { "content-type": "application/json" }
        });
      }
    }

    const kv = await getKV(env);
    const listResult = await kv.list({ prefix: "USER_" });
    let users = [];

    for (const item of (listResult.keys || [])) {
      try {
        let user = await kv.get(item.name, "json");
        if (user && user.is_active) {
          users.push({
            chat_id: String(user.chat_id || item.name.replace(/^USER_/, "")),
            username: user.username || null,
            first_name: user.first_name || null,
            deal_type: user.deal_type || "sale",
            price_min_usd: user.price_min_usd,
            price_max_usd: user.price_max_usd,
            rent_price_min_usd: user.deal_type === "rent" ? (user.price_min_usd || 300) : (user.rent_price_min_usd !== undefined ? user.rent_price_min_usd : 300),
            rent_price_max_usd: user.deal_type === "rent" ? (user.price_max_usd || 1500) : (user.rent_price_max_usd !== undefined ? user.rent_price_max_usd : 1500),
            area_min_m2: user.area_min_m2,
            area_max_m2: user.area_max_m2,
            rooms_min: (user.rooms_min !== undefined && user.rooms_min !== null) ? user.rooms_min : 2,
            districts: user.districts || DEFAULT_USER_PROFILE.districts,
            owner_type: user.owner_type || "all",
            is_active: user.is_active
          });
        }
      } catch (err) {
        console.error("Error reading user item:", item.name, err);
      }
    }

    // If no users found in KV yet, populate and return default admin profile so it's never empty!
    if (users.length === 0) {
      const defaultAdmin = {
        chat_id: "1105321687",
        username: "iashvilisandro7",
        first_name: "Sandro",
        ...DEFAULT_USER_PROFILE,
        is_active: true
      };
      try {
        await kv.put("USER_1105321687", JSON.stringify(defaultAdmin));
      } catch (e) {}
      users = [defaultAdmin];
    }

    return new Response(JSON.stringify(users, null, 2), {
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store, no-cache, must-revalidate, proxy-revalidate",
        "pragma": "no-cache",
        "expires": "0"
      }
    });
  } catch (fatalErr) {
    return new Response(JSON.stringify({ error: fatalErr.message, stack: fatalErr.stack }), {
      status: 500,
      headers: { "content-type": "application/json; charset=utf-8" }
    });
  }
}

/**
 * Protected D1 Database API for Python Runner: query & batch operations
 */
async function handleDbRequest(request, env) {
  try {
    const expectedKey = (env.SYNC_KEY || "").trim();
    if (expectedKey) {
      const syncKey = (request.headers.get("X-Sync-Key") || new URL(request.url).searchParams.get("key") || "").trim();
      if (syncKey !== expectedKey) {
        return new Response(JSON.stringify({ success: false, error: "Unauthorized" }), {
          status: 401,
          headers: { "content-type": "application/json" }
        });
      }
    }

    if (!env.DB || typeof env.DB.prepare !== "function") {
      return new Response(JSON.stringify({ success: false, error: "D1 database binding 'DB' not configured in Worker" }), {
        status: 500,
        headers: { "content-type": "application/json" }
      });
    }

    const body = await request.json();

    // 1. Batch queries: { batch: [ { sql: "...", params: [...] }, ... ] }
    if (Array.isArray(body.batch)) {
      const stmts = body.batch.map(q => {
        const stmt = env.DB.prepare(q.sql);
        return (q.params && Array.isArray(q.params) && q.params.length > 0)
          ? stmt.bind(...q.params)
          : stmt;
      });
      const results = await env.DB.batch(stmts);
      // Map batch results to clean output: array of { results: [...], meta: {...} }
      const cleanResults = results.map(r => ({
        results: r.results || [],
        meta: r.meta || {}
      }));
      return new Response(JSON.stringify({ success: true, batch_results: cleanResults }), {
        headers: { "content-type": "application/json" }
      });
    }

    // 2. Single query: { sql: "...", params: [...] }
    if (body.sql) {
      let stmt = env.DB.prepare(body.sql);
      if (body.params && Array.isArray(body.params) && body.params.length > 0) {
        stmt = stmt.bind(...body.params);
      }
      const res = await stmt.all();
      return new Response(JSON.stringify({
        success: true,
        results: res.results || [],
        meta: res.meta || {}
      }), {
        headers: { "content-type": "application/json" }
      });
    }

    return new Response(JSON.stringify({ success: false, error: "Invalid request payload. Expected 'sql' or 'batch'." }), {
      status: 400,
      headers: { "content-type": "application/json" }
    });
  } catch (err) {
    return new Response(JSON.stringify({ success: false, error: err.message }), {
      status: 500,
      headers: { "content-type": "application/json" }
    });
  }
}

/**
 * Handles incoming Telegram Webhook payloads
 */
async function handleTelegramWebhook(request, env) {
  const token = env.TELEGRAM_BOT_TOKEN;
  if (!token) {
    return new Response(JSON.stringify({ error: "TELEGRAM_BOT_TOKEN not configured" }), { status: 500 });
  }

  let update;
  try {
    update = await request.json();
  } catch (e) {
    return new Response("Invalid JSON", { status: 400 });
  }

  const kv = await getKV(env);

  try {
    // 1. Handle Callback Queries (Button clicks)
    if (update.callback_query) {
      await handleCallbackQuery(update.callback_query, token, kv);
      return new Response("OK");
    }

    // 2. Handle Text Messages
    if (update.message && update.message.text) {
      await handleMessage(update.message, token, kv, env);
      return new Response("OK");
    }
  } catch (err) {
    console.error("Error handling Telegram update:", err);
    return new Response(`OK (handled error: ${err.message})`, { status: 200 });
  }

  return new Response("OK");
}

/**
 * Processes text messages & bot commands
 */
async function handleMessage(msg, token, kv, env) {
  const chatId = msg.chat.id;
  const text = msg.text.trim();
  const userKey = `USER_${chatId}`;
  let user = await kv.get(userKey, "json");

  if (!user) {
    user = {
      ...DEFAULT_USER_PROFILE,
      chat_id: chatId,
      username: msg.from?.username || "",
      first_name: msg.from?.first_name || "",
      created_at: new Date().toISOString()
    };
  }

  // Check state: awaiting price or area input
  if (user.state === "awaiting_price") {
    const nums = text.match(/\d+/g);
    if (nums && nums.length >= 2) {
      const minP = Math.min(parseInt(nums[0]), parseInt(nums[1]));
      const maxP = Math.max(parseInt(nums[0]), parseInt(nums[1]));
      user.price_min_usd = minP;
      user.price_max_usd = maxP;
      user.state = null;
      user.updated_at = new Date().toISOString();
      await kv.put(userKey, JSON.stringify(user));

      await sendTelegram(token, "sendMessage", {
        chat_id: chatId,
        text: `✅ <b>ფასის დიაპაზონი განახლდა:</b>\n💰 <b>$${minP.toLocaleString()} – $${maxP.toLocaleString()}</b>`,
        parse_mode: "HTML",
        reply_markup: getMainKeyboard(user)
      });
      return;
    } else {
      await sendTelegram(token, "sendMessage", {
        chat_id: chatId,
        text: `⚠️ <b>არასწორი ფორმატი!</b>\nგთხოვთ შეიყვანოთ 2 რიცხვი (მინიმალური და მაქსიმალური ფასი დოლარში).\nმაგალითად: <code>45000 70000</code>`,
        parse_mode: "HTML"
      });
      return;
    }
  }

  if (user.state === "awaiting_area") {
    const nums = text.match(/\d+/g);
    if (nums && nums.length >= 2) {
      const minA = Math.min(parseInt(nums[0]), parseInt(nums[1]));
      const maxA = Math.max(parseInt(nums[0]), parseInt(nums[1]));
      user.area_min_m2 = minA;
      user.area_max_m2 = maxA;
      user.state = null;
      user.updated_at = new Date().toISOString();
      await kv.put(userKey, JSON.stringify(user));

      await sendTelegram(token, "sendMessage", {
        chat_id: chatId,
        text: `✅ <b>ფართობის დიაპაზონი განახლდა:</b>\n📐 <b>${minA} მ² – ${maxA} მ²</b>`,
        parse_mode: "HTML",
        reply_markup: getMainKeyboard(user)
      });
      return;
    } else {
      await sendTelegram(token, "sendMessage", {
        chat_id: chatId,
        text: `⚠️ <b>არასწორი ფორმატი!</b>\nგთხოვთ შეიყვანოთ 2 რიცხვი (მინიმალური და მაქსიმალური კვადრატულობა).\nმაგალითად: <code>45 65</code>`,
        parse_mode: "HTML"
      });
      return;
    }
  }

  // Direct price command: /price 10000 100000 or /ფასი 10000 100000
  if (text.startsWith("/price") || text.startsWith("/ფასი")) {
    const nums = text.match(/\d+/g);
    if (nums && nums.length >= 2) {
      const minP = Math.min(parseInt(nums[0]), parseInt(nums[1]));
      const maxP = Math.max(parseInt(nums[0]), parseInt(nums[1]));
      user.price_min_usd = minP;
      user.price_max_usd = maxP;
      user.state = null;
      user.updated_at = new Date().toISOString();
      await kv.put(userKey, JSON.stringify(user));

      await sendTelegram(token, "sendMessage", {
        chat_id: chatId,
        text: `✅ <b>ფასის დიაპაზონი განახლდა:</b>\n💰 <b>$${minP.toLocaleString()} – $${maxP.toLocaleString()}</b>`,
        parse_mode: "HTML",
        reply_markup: getMainKeyboard(user)
      });
      return;
    }
  }

  // Direct area command: /area 10 100 or /ფართობი 10 100
  if (text.startsWith("/area") || text.startsWith("/ფართობი")) {
    const nums = text.match(/\d+/g);
    if (nums && nums.length >= 2) {
      const minA = Math.min(parseInt(nums[0]), parseInt(nums[1]));
      const maxA = Math.max(parseInt(nums[0]), parseInt(nums[1]));
      user.area_min_m2 = minA;
      user.area_max_m2 = maxA;
      user.state = null;
      user.updated_at = new Date().toISOString();
      await kv.put(userKey, JSON.stringify(user));

      await sendTelegram(token, "sendMessage", {
        chat_id: chatId,
        text: `✅ <b>ფართობის დიაპაზონი განახლდა:</b>\n📐 <b>${minA} მ² – ${maxA} მ²</b>`,
        parse_mode: "HTML",
        reply_markup: getMainKeyboard(user)
      });
      return;
    }
  }

  // Handle standard commands
  if (text.startsWith("/start") || text.startsWith("/help")) {
    user.state = null;
    await kv.put(userKey, JSON.stringify(user));
    const welcome = (
      `🏢 <b>მოგესალმებით Tbilisi Real Estate Radar-ში!</b>\n\n` +
      `ეს ბოტი ავტომატურად ასკანერებს <b>MyHome.ge</b> და <b>SS.ge</b> პორტალებს 24/7 რეჟიმში და ახალ ბინებს მომენტალურად გიგზავნით.\n` +
      `🔍 <b>რენტგენი (X-Ray):</b> ჩააგდეთ ჩატში ბინის ლინკი, ტელეფონის ნომერი ან FB პოსტის ტექსტი გადასამოწმებლად.\n\n` +
      formatSettingsSummary(user)
    );
    await sendTelegram(token, "sendMessage", {
      chat_id: chatId,
      text: welcome,
      parse_mode: "HTML",
      reply_markup: getMainKeyboard(user)
    });
    return;
  }

  if (text.startsWith("/filter") || text.startsWith("/settings")) {
    user.state = null;
    await kv.put(userKey, JSON.stringify(user));
    await sendTelegram(token, "sendMessage", {
      chat_id: chatId,
      text: formatSettingsSummary(user),
      parse_mode: "HTML",
      reply_markup: getMainKeyboard(user)
    });
    return;
  }

  // Owner filter command: /owner [owner|agent|all] or /მესაკუთრე
  if (text.startsWith("/owner") || text.startsWith("/მესაკუთრე")) {
    const parts = text.split(/\s+/);
    if (parts.length > 1) {
      const arg = parts[1].toLowerCase();
      if (arg === "owner" || arg === "მესაკუთრე") {
        user.owner_type = "owner";
      } else if (arg === "agent" || arg === "agency" || arg === "სააგენტო") {
        user.owner_type = "agent";
      } else if (arg === "all" || arg === "both" || arg === "ყველა" || arg === "ორივე") {
        user.owner_type = "all";
      }
    } else {
      if (!user.owner_type || user.owner_type === "all") {
        user.owner_type = "owner";
      } else if (user.owner_type === "owner") {
        user.owner_type = "agent";
      } else {
        user.owner_type = "all";
      }
    }
    user.state = null;
    user.updated_at = new Date().toISOString();
    await kv.put(userKey, JSON.stringify(user));

    await sendTelegram(token, "sendMessage", {
      chat_id: chatId,
      text: formatSettingsSummary(user),
      parse_mode: "HTML",
      reply_markup: getMainKeyboard(user)
    });
    return;
  }

  // On-Demand X-Ray for MyHome/SS.ge URLs, Georgian phone numbers, or Facebook apartment texts
  const xrayReply = await handleXRayQuery(text, env);
  if (xrayReply) {
    await sendTelegram(token, "sendMessage", {
      chat_id: chatId,
      text: xrayReply,
      parse_mode: "HTML",
      disable_web_page_preview: true
    });
    return;
  }

  // Fallback for unrecognized text
  await sendTelegram(token, "sendMessage", {
    chat_id: chatId,
    text: `გთხოვთ გამოიყენოთ მენიუ ან ბრძანება /filter პარამეტრების სამართავად, ან ჩააგდეთ ბინის ლინკი / ტელეფონის ნომერი „რენტგენზე“ შესამოწმებლად.`,
    reply_markup: getMainKeyboard(user)
  });
}

/**
 * Processes inline button clicks (Callback Queries)
 */
async function handleCallbackQuery(cb, token, kv) {
  const chatId = cb.message.chat.id;
  const messageId = cb.message.message_id;
  const data = cb.data;
  const userKey = `USER_${chatId}`;
  let user = (await kv.get(userKey, "json")) || { ...DEFAULT_USER_PROFILE, chat_id: chatId };

  // Answer callback to remove loading state in Telegram client
  await sendTelegram(token, "answerCallbackQuery", { callback_query_id: cb.id });

  if (data === "menu_price") {
    user.state = "awaiting_price";
    await kv.put(userKey, JSON.stringify(user));
    await sendTelegram(token, "sendMessage", {
      chat_id: chatId,
      text: `💰 <b>ფასის შეცვლა</b>\n\nმიმდინარე: $${user.price_min_usd.toLocaleString()} – $${user.price_max_usd.toLocaleString()}\n\nგთხოვთ მომწეროთ ახალი დიაპაზონი (მაგ: <code>45000 70000</code>):`,
      parse_mode: "HTML"
    });
    return;
  }

  if (data === "menu_area") {
    user.state = "awaiting_area";
    await kv.put(userKey, JSON.stringify(user));
    await sendTelegram(token, "sendMessage", {
      chat_id: chatId,
      text: `📐 <b>ფართობის შეცვლა</b>\n\nმიმდინარე: ${user.area_min_m2} მ² – ${user.area_max_m2} მ²\n\nგთხოვთ მომწეროთ ახალი დიაპაზონი (მაგ: <code>48 65</code>):`,
      parse_mode: "HTML"
    });
    return;
  }

  if (data === "menu_districts") {
    await sendTelegram(token, "editMessageText", {
      chat_id: chatId,
      message_id: messageId,
      text: `📍 <b>უბნების მართვა:</b>\nდააჭირეთ ღილაკს უბნის ჩასართავად ან გამოსართავად:`,
      parse_mode: "HTML",
      reply_markup: getDistrictsKeyboard(user)
    });
    return;
  }

  if (data.startsWith("toggle_dist:")) {
    const district = data.replace("toggle_dist:", "");
    let current = user.districts || [];
    if (current.includes(district)) {
      current = current.filter(d => d !== district);
    } else {
      current.push(district);
    }
    user.districts = current;
    user.updated_at = new Date().toISOString();
    await kv.put(userKey, JSON.stringify(user));

    await sendTelegram(token, "editMessageReplyMarkup", {
      chat_id: chatId,
      message_id: messageId,
      reply_markup: getDistrictsKeyboard(user)
    });
    return;
  }

  if (data === "toggle_active") {
    user.is_active = !user.is_active;
    user.updated_at = new Date().toISOString();
    await kv.put(userKey, JSON.stringify(user));

    await sendTelegram(token, "editMessageText", {
      chat_id: chatId,
      message_id: messageId,
      text: formatSettingsSummary(user),
      parse_mode: "HTML",
      reply_markup: getMainKeyboard(user)
    });
    return;
  }

  if (data === "toggle_rooms") {
    user.rooms_min = (user.rooms_min === 2) ? 1 : 2;
    user.updated_at = new Date().toISOString();
    await kv.put(userKey, JSON.stringify(user));

    await sendTelegram(token, "editMessageText", {
      chat_id: chatId,
      message_id: messageId,
      text: formatSettingsSummary(user),
      parse_mode: "HTML",
      reply_markup: getMainKeyboard(user)
    });
    return;
  }

  if (data === "toggle_deal_type") {
    if (!user.deal_type || user.deal_type === "sale") {
      user.deal_type = "rent";
      if (user.price_min_usd > 3000) {
        user.price_min_usd = 300;
        user.price_max_usd = 1200;
      }
    } else if (user.deal_type === "rent") {
      user.deal_type = "both";
      if (!user.rent_price_min_usd) user.rent_price_min_usd = 300;
      if (!user.rent_price_max_usd) user.rent_price_max_usd = 1500;
      if (user.price_max_usd < 5000) {
        user.price_min_usd = 45000;
        user.price_max_usd = 75000;
      }
    } else {
      user.deal_type = "sale";
      if (user.price_max_usd < 5000) {
        user.price_min_usd = 45000;
        user.price_max_usd = 75000;
      }
    }
    user.updated_at = new Date().toISOString();
    await kv.put(userKey, JSON.stringify(user));

    await sendTelegram(token, "editMessageText", {
      chat_id: chatId,
      message_id: messageId,
      text: formatSettingsSummary(user),
      parse_mode: "HTML",
      reply_markup: getMainKeyboard(user)
    });
    return;
  }

  if (data === "toggle_owner_type") {
    if (!user.owner_type || user.owner_type === "all") {
      user.owner_type = "owner";
    } else if (user.owner_type === "owner") {
      user.owner_type = "agent";
    } else {
      user.owner_type = "all";
    }
    user.updated_at = new Date().toISOString();
    await kv.put(userKey, JSON.stringify(user));

    await sendTelegram(token, "editMessageText", {
      chat_id: chatId,
      message_id: messageId,
      text: formatSettingsSummary(user),
      parse_mode: "HTML",
      reply_markup: getMainKeyboard(user)
    });
    return;
  }

  if (data === "reset_defaults") {
    user = {
      ...DEFAULT_USER_PROFILE,
      chat_id: chatId,
      username: user.username,
      first_name: user.first_name,
      updated_at: new Date().toISOString()
    };
    await kv.put(userKey, JSON.stringify(user));

    await sendTelegram(token, "editMessageText", {
      chat_id: chatId,
      message_id: messageId,
      text: `🔄 <b>პარამეტრები დაბრუნდა საწყისზე:</b>\n\n` + formatSettingsSummary(user),
      parse_mode: "HTML",
      reply_markup: getMainKeyboard(user)
    });
    return;
  }

  if (data === "menu_main") {
    await sendTelegram(token, "editMessageText", {
      chat_id: chatId,
      message_id: messageId,
      text: formatSettingsSummary(user),
      parse_mode: "HTML",
      reply_markup: getMainKeyboard(user)
    });
    return;
  }
}

/**
 * Formats a clean HTML summary of user settings
 */
function formatSettingsSummary(user) {
  const statusIcon = user.is_active ? "🟢 <b>აქტიური (იგზავნება)</b>" : "⏸️ <b>დაპაუზებული</b>";
  const districtsStr = (user.districts && user.districts.length > 0)
    ? user.districts.join(", ")
    : "<i>ყველა უბანი</i>";

  let dealTypeStr = "🏠 <b>იყიდება</b>";
  let priceStr = `$${user.price_min_usd?.toLocaleString()} – $${user.price_max_usd?.toLocaleString()}`;
  if (user.deal_type === "rent") {
    dealTypeStr = "🔑 <b>ქირავდება</b>";
    priceStr = `$${user.price_min_usd?.toLocaleString()} – $${user.price_max_usd?.toLocaleString()} / თვე`;
  } else if (user.deal_type === "both") {
    dealTypeStr = "🔄 <b>იყიდება + ქირავდება</b>";
    const rMin = user.rent_price_min_usd || 300;
    const rMax = user.rent_price_max_usd || 1500;
    priceStr = `იყიდება: $${user.price_min_usd?.toLocaleString()}–$${user.price_max_usd?.toLocaleString()} | ქირა: $${rMin}–$${rMax}/თვე`;
  }

  let ownerTypeStr = "👥 <b>ყველა (მესაკუთრე + სააგენტო)</b>";
  if (user.owner_type === "owner") {
    ownerTypeStr = "🔑 <b>მხოლოდ მესაკუთრე (Owner)</b>";
  } else if (user.owner_type === "agent") {
    ownerTypeStr = "🏢 <b>მხოლოდ სააგენტო (Agent)</b>";
  }

  return (
    `⚙️ <b>თქვენი საძიებო პარამეტრები:</b>\n\n` +
    `🏷️ <b>ტიპი:</b> ${dealTypeStr}\n` +
    `👤 <b>განმცხადებელი:</b> ${ownerTypeStr}\n` +
    `💰 <b>ფასი:</b> ${priceStr}\n` +
    `📐 <b>ფართობი:</b> ${user.area_min_m2} – ${user.area_max_m2} მ²\n` +
    `🚪 <b>ოთახები:</b> მინიმუმ ${user.rooms_min || 2} ოთახი\n` +
    `📍 <b>უბნები:</b> ${districtsStr}\n` +
    `🔔 <b>სტატუსი:</b> ${statusIcon}\n\n` +
    `<i>პარამეტრების შესაცვლელად გამოიყენეთ ქვემოთ მოცემული ღილაკები:</i>`
  );
}

/**
 * Builds the main inline keyboard
 */
function getMainKeyboard(user) {
  const activeLabel = user.is_active ? "⏸️ დაპაუზება" : "▶️ ჩართვა";
  const roomsLabel = user.rooms_min === 1 ? "🚪 ოთახები: 1+" : "🚪 ოთახები: 2+";
  let dealTypeLabel = "🏷️ ტიპი: იყიდება 🏠";
  if (user.deal_type === "rent") {
    dealTypeLabel = "🏷️ ტიპი: ქირავდება 🔑";
  } else if (user.deal_type === "both") {
    dealTypeLabel = "🏷️ ტიპი: ორივე 🔄";
  }

  let ownerTypeLabel = "👤 განმცხადებელი: ყველა (ორივე) 👥";
  if (user.owner_type === "owner") {
    ownerTypeLabel = "👤 განმცხადებელი: მხოლოდ მესაკუთრე 🔑";
  } else if (user.owner_type === "agent") {
    ownerTypeLabel = "👤 განმცხადებელი: მხოლოდ სააგენტო 🏢";
  }

  return {
    inline_keyboard: [
      [
        { text: dealTypeLabel, callback_data: "toggle_deal_type" }
      ],
      [
        { text: ownerTypeLabel, callback_data: "toggle_owner_type" }
      ],
      [
        { text: "💰 ფასის შეცვლა", callback_data: "menu_price" },
        { text: "📐 ფართობის შეცვლა", callback_data: "menu_area" }
      ],
      [
        { text: "📍 უბნების არჩევა", callback_data: "menu_districts" },
        { text: roomsLabel, callback_data: "toggle_rooms" }
      ],
      [
        { text: activeLabel, callback_data: "toggle_active" },
        { text: "🔄 საწყისზე დაბრუნება", callback_data: "reset_defaults" }
      ]
    ]
  };
}

/**
 * Builds the district toggle keyboard (2 columns)
 */
function getDistrictsKeyboard(user) {
  const selected = user.districts || [];
  const rows = [];
  let currentRow = [];

  for (const d of DEFAULT_DISTRICTS) {
    const isChecked = selected.includes(d);
    const icon = isChecked ? "✅" : "▫️";
    currentRow.push({
      text: `${icon} ${d}`,
      callback_data: `toggle_dist:${d}`
    });

    if (currentRow.length === 2) {
      rows.push(currentRow);
      currentRow = [];
    }
  }

  if (currentRow.length > 0) {
    rows.push(currentRow);
  }

  rows.push([
    { text: "⬅️ მთავარ მენიუში დაბრუნება", callback_data: "menu_main" }
  ]);

  return { inline_keyboard: rows };
}

/**
 * Helper to call Telegram Bot API
 */
async function sendTelegram(token, method, payload) {
  try {
    const res = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload)
    });
    return await res.json();
  } catch (e) {
    console.error(`Telegram API error [${method}]:`, e);
    return null;
  }
}

/**
 * ============================================================================
 * ON-DEMAND X-RAY ("რენტგენი") FOR LINKS, PHONE NUMBERS & FACEBOOK POST TEXT
 * ============================================================================
 */
const XRAY_DISTRICT_KEYWORDS = [
  ["საბურთალო", "საბურთალო"],
  ["ვაკე", "ვაკე"],
  ["ვაკის", "ვაკე"],
  ["დიდი დიღომ", "დიდი დიღომი"],
  ["დიღმის მასივ", "დიღმის მასივი"],
  ["დიღომ", "დიღომი"],
  ["ისან", "ისანი"],
  ["სამგორ", "სამგორი"],
  ["ვარკეთილ", "ვარკეთილი"],
  ["გლდან", "გლდანი"],
  ["მუხიან", "მუხიანი"],
  ["ნაძალადევ", "ნაძალადევი"],
  ["სანზონ", "სანზონა"],
  ["ჩუღურეთ", "ჩუღურეთი"],
  ["მთაწმინდ", "მთაწმინდა"],
  ["სოლოლაკ", "სოლოლაკი"],
  ["კრწანის", "კრწანისი"],
  ["ორთაჭალ", "ორთაჭალა"],
  ["ავლაბარ", "ავლაბარი"],
  ["ავლაბრ", "ავლაბარი"],
  ["დიდუბ", "დიდუბე"],
  ["წერეთელ", "წერეთელი"],
  ["წერეთლ", "წერეთელი"],
  ["თემქ", "თემქა"],
  ["ვაშლიჯვარ", "ვაშლიჯვარი"],
  ["ნუცუბიძ", "ნუცუბიძე"],
  ["ვაზისუბან", "ვაზისუბანი"],
  ["ვაზისუბნ", "ვაზისუბანი"],
  ["ბაგებ", "ბაგები"],
  ["ლის", "ლისი"]
];

function formatGeorgianPhone(phone9) {
  return `+995 ${phone9.slice(0, 3)} ${phone9.slice(3, 5)} ${phone9.slice(5, 7)} ${phone9.slice(7, 9)}`;
}

function extract9DigitPhone(text) {
  if (!text) return null;
  const cleaned = String(text).replace(/https?:\/\/\S+/g, " ");
  const m = cleaned.match(/(?:\+?995[\s\-.]*)?(5\d{2}[\s\-.]*\d{2}[\s\-.]*\d{2}[\s\-.]*\d{2}|\b5\d{8}\b)/);
  if (!m) return null;
  const digits = m[1].replace(/\D/g, "");
  return (digits.length === 9 && digits.startsWith("5")) ? digits : null;
}

function parseXRayInput(text) {
  if (!text || typeof text !== "string") return null;
  const stripped = text.trim();
  if (!stripped || stripped.startsWith("/")) return null;
  if (/^\d+(?:\.\d+)?\s*[-\s]\s*\d+(?:\.\d+)?$/.test(stripped)) return null;

  const mhMatch = stripped.match(/https?:\/\/(?:www\.)?myhome\.ge\/(?:[a-z]{2}\/)?pr\/(\d+)(?:\/[^\s]*)?/i);
  if (mhMatch) {
    return {
      kind: "url",
      source: "myhome",
      sourceId: mhMatch[1],
      listingId: `myhome_${mhMatch[1]}`,
      url: mhMatch[0],
      phone9: extract9DigitPhone(stripped)
    };
  }

  const ssMatch = stripped.match(/https?:\/\/(?:home\.|www\.)?ss\.ge\/[^\s]*?-(\d{5,12})\b/i);
  if (ssMatch) {
    return {
      kind: "url",
      source: "ss_ge",
      sourceId: ssMatch[1],
      listingId: `ss_ge_${ssMatch[1]}`,
      url: ssMatch[0],
      phone9: extract9DigitPhone(stripped)
    };
  }

  let areaM2 = null;
  const areaMatch = stripped.match(/\b(\d{2,3}(?:[.,]\d+)?)\s*(?:კვ\.?\s*მ|კვადრატ\S*|მ²|მ2|m2|m²|sqm)/i);
  if (areaMatch) areaM2 = parseFloat(areaMatch[1].replace(",", "."));

  let floor = null;
  const floorMatch = stripped.match(/(?:მე[-\s]*)?(\d{1,2})(?:\s*\/\s*\d{1,2})?\s*სართულ\S*|სართულ\S*\s*[:-]?\s*(\d{1,2})/i);
  if (floorMatch) floor = floorMatch[1] || floorMatch[2];

  let rooms = null;
  const roomsMatch = stripped.match(/\b(\d)\s*[-\s]*ოთახ\S*/i);
  if (roomsMatch) rooms = parseInt(roomsMatch[1], 10);

  let priceUsd = null;
  let priceMatch = stripped.match(/(?:\$\s*(\d{2,3}(?:[,\s]\d{3})+|\d{4,6})|(\d{2,3}(?:[,\s]\d{3})+|\d{4,6})\s*(?:\$|დოლარ\S*|usd))/i);
  if (!priceMatch) {
    priceMatch = stripped.match(/ფასი\s*[:-]?\s*(\d{2,3}(?:[,\s]\d{3})+|\d{4,6})\b/i);
  }
  if (priceMatch) {
    const rawP = priceMatch[1] || priceMatch[2];
    const val = parseFloat(rawP.replace(/[,\s]/g, ""));
    if (val >= 10000 && val <= 2000000) priceUsd = val;
  }

  let district = null;
  const lower = stripped.toLowerCase();
  for (const [stem, canonical] of XRAY_DISTRICT_KEYWORDS) {
    if (lower.includes(stem)) {
      district = canonical;
      break;
    }
  }

  const phone9 = extract9DigitPhone(stripped);
  const hasFbSignal = areaM2 !== null && (district !== null || priceUsd !== null || floor !== null || rooms !== null);
  if (hasFbSignal) {
    return {
      kind: "fb_text",
      phone9,
      formattedPhone: phone9 ? formatGeorgianPhone(phone9) : null,
      area_m2: areaM2,
      floor,
      rooms,
      price_usd: priceUsd,
      district
    };
  }

  if (phone9) {
    return {
      kind: "phone",
      phone9,
      formattedPhone: formatGeorgianPhone(phone9)
    };
  }

  return null;
}

function parseSsGeStatementDetails(st, agentInfo = {}, html = "", sourceId = "", pageUrl = "") {
  const firstPhone = Array.isArray(st.applicationPhones) && st.applicationPhones.length > 0 ? st.applicationPhones[0] : null;
  const rawPhone = (firstPhone && (firstPhone.phoneNumber || firstPhone)) || st.user_phone_number || st.phoneNumber || st.phone_number || "";
  const rawDesc = typeof st.description === "object" && st.description !== null
    ? (st.description.ka || st.description.text || "")
    : String(st.comment || st.description || "");
  const phone9 = extract9DigitPhone(`${rawPhone} ${rawDesc}`);

  const agencyId = st.agencyId || st.externalCompanyId || agentInfo.companyId || null;
  const agencyName = st.agencyName || st.companyName || agentInfo.companyName || null;
  const htmlEntityMatch = html ? html.match(/"userEntityType"\s*:\s*"([^"]+)"/i) : null;
  const uet = String(
    st.userEntityType || st.userType || st.authorType || agentInfo.userEntityType || (htmlEntityMatch ? htmlEntityMatch[1] : "")
  ).toLowerCase();
  const appCount = Number(st.userApplicationCount || st.userStatementsCount || st.user_statements_count || agentInfo.applicationCount || 0) || 0;
  const infoType = Number(agentInfo.infoType ?? st.infoType ?? 0);

  const htmlHasAgentBadge = Boolean(
    html && (/>[\s]*აგენტი[\s]*</.test(html) || />[\s]*სააგენტო[\s]*</.test(html) || />[\s]*მაკლერი[\s]*</.test(html))
  );
  const htmlHasOwnerBadge = Boolean(
    html && (/>[\s]*მესაკუთრე[\s]*</.test(html) || />[\s]*ფიზიკური პირი[\s]*</.test(html))
  );

  const isBrokerSignal = Boolean(
    agencyId ||
    agencyName ||
    st.isForAgency ||
    st.isAgency === true ||
    st.isOwner === false ||
    st.commercialRealEstateType ||
    st.userType === 2 ||
    agentInfo.companyId != null ||
    infoType === 2 ||
    ["broker", "agent", "agency", "company", "developer", "აგენტ", "მაკლერ", "სააგენტო"].some(k => uet.includes(k)) ||
    appCount >= 3 ||
    htmlHasAgentBadge
  );

  const isVerifiedOwner = !isBrokerSignal && Boolean(
    ["individual", "owner", "physical", "მესაკუთრე", "ფიზიკური"].some(k => uet.includes(k)) ||
    st.isOwner === true ||
    st.isAgency === false ||
    htmlHasOwnerBadge
  );

  const isOwner = isBrokerSignal ? 0 : (isVerifiedOwner ? 1 : null);
  const userType = isBrokerSignal ? (agencyId || agencyName ? "agency" : "agent") : (isVerifiedOwner ? "physical" : null);
  const rawUid = st.userId || st.user_id || agencyId;

  return {
    id: `ss_ge_${sourceId}`,
    source: "ss_ge",
    source_id: String(sourceId),
    url: pageUrl || `https://home.ss.ge/ka/udzravi-qoneba/${sourceId}`,
    price_usd: Number(st.price?.priceUsd || st.priceUsd || st.price_usd || st.total_price || 0),
    area_m2: Number(st.totalArea || st.area || 0),
    floor: String(st.floorNumber ?? st.floor ?? ""),
    rooms: Number(st.numberOfRooms || st.rooms || st.room || 0),
    district: st.address?.subdistrictTitle || st.address?.districtTitle || st.district_name || "",
    is_owner: isOwner,
    user_type: userType,
    phone_number: phone9 ? formatGeorgianPhone(phone9) : null,
    author_id: rawUid ? String(rawUid) : null,
    author_listings_count: appCount || null
  };
}

function mergeLiveIntoRow(row, live) {
  if (!live) return row;
  if (!row) return live;
  const merged = { ...row };
  if (live.is_owner === 0 || live.is_owner === false) {
    merged.is_owner = 0;
  } else if ((merged.is_owner === null || merged.is_owner === undefined) && live.is_owner !== null && live.is_owner !== undefined) {
    merged.is_owner = live.is_owner ? 1 : 0;
  }
  if (live.user_type && (merged.is_owner === 0 || !merged.user_type)) {
    merged.user_type = live.user_type;
  }
  if (!merged.phone_number && live.phone_number) {
    merged.phone_number = live.phone_number;
  }
  if (!merged.author_id && live.author_id) {
    merged.author_id = String(live.author_id);
  }
  const prevCnt = parseInt(merged.author_listings_count, 10) || 0;
  const liveCnt = parseInt(live.author_listings_count, 10) || 0;
  if (liveCnt > 0 || prevCnt > 0) {
    merged.author_listings_count = Math.max(prevCnt, liveCnt);
  }
  return merged;
}

async function persistLiveEnrichment(db, rowId, merged) {
  if (!db || !rowId || !merged) return;
  try {
    await db
      .prepare(
        "UPDATE properties SET is_owner = COALESCE(?, is_owner), user_type = COALESCE(?, user_type), phone_number = COALESCE(?, phone_number), author_id = COALESCE(?, author_id), author_listings_count = COALESCE(?, author_listings_count) WHERE id = ?"
      )
      .bind(
        merged.is_owner !== undefined ? merged.is_owner : null,
        merged.user_type || null,
        merged.phone_number || null,
        merged.author_id || null,
        merged.author_listings_count || null,
        rowId
      )
      .run();
  } catch (e) {}
}

async function fetchLiveStatementDetails(source, sourceId, rawUrl) {
  const headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ka,en-US;q=0.9,en;q=0.8"
  };

  try {
    if (source === "myhome") {
      const apiUrl = `https://api-statements.tnet.ge/v1/statements/${sourceId}`;
      const res = await fetch(apiUrl, { headers: { ...headers, "Origin": "https://www.myhome.ge", "Referer": "https://www.myhome.ge/" } });
      if (res.ok) {
        const json = await res.json();
        const st = json?.data?.statement || json?.data || {};
        const phone9 = extract9DigitPhone(st.user_phone_number || st.phone_number || st.comment || "");
        const ut = String(st.user_type?.type || st.user_type || "").toLowerCase();
        const stmtCnt = Number(st.user_statements_count || st.statements_count || 0) || 0;
        const isBroker = st.is_owner === false || ["agent", "agency", "broker", "developer", "company"].some(k => ut.includes(k)) || stmtCnt >= 3;
        const isOwner = isBroker ? 0 : (ut === "physical" || ut === "owner" || st.is_owner === true ? 1 : null);
        return {
          id: `myhome_${sourceId}`,
          source: "myhome",
          source_id: String(sourceId),
          url: rawUrl || `https://www.myhome.ge/ka/pr/${sourceId}/`,
          price_usd: Number(st.price?.[2]?.price_total || st.price_usd || st.total_price || 0),
          area_m2: Number(st.area || 0),
          floor: String(st.floor || ""),
          rooms: Number(st.room_type_id || st.rooms || 0),
          district: st.district_name || st.urban_name || "",
          is_owner: isOwner,
          user_type: ut || (isBroker ? "agent" : null),
          phone_number: phone9 ? formatGeorgianPhone(phone9) : null,
          author_id: st.user_id ? String(st.user_id) : null,
          author_listings_count: stmtCnt || null
        };
      }
    }

    if (source === "ss_ge") {
      try {
        const tokenRes = await fetch("https://home.ss.ge/api/refresh_access_token", {
          method: "POST",
          headers: {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://home.ss.ge",
            "Referer": "https://home.ss.ge/"
          }
        });
        const setCookie = tokenRes.headers.get("set-cookie") || "";
        const tokenMatch = setCookie.match(/ss-session-token=([^;\s,]+)/);
        const token = tokenMatch ? tokenMatch[1] : (tokenRes.headers.get("x-ss-session-token") || null);
        if (token) {
          const detailsUrl = `https://api-gateway.ss.ge/v1/RealEstate/details?applicationId=${encodeURIComponent(sourceId)}&currencyId=2&updateViewCount=false`;
          const detailsRes = await fetch(detailsUrl, {
            method: "PUT",
            headers: {
              "Authorization": `Bearer ${token}`,
              "Accept": "application/json, text/plain, */*",
              "Accept-Language": "ka",
              "os": "web",
              "Origin": "https://home.ss.ge",
              "Referer": "https://home.ss.ge/",
              "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            }
          });
          if (detailsRes.ok) {
            const st = await detailsRes.json();
            if (st && Number(st.applicationId || sourceId) === Number(sourceId)) {
              const parsedApi = parseSsGeStatementDetails(st, st.initialAgentInfoData || {}, "", sourceId, rawUrl);
              if (parsedApi.is_owner !== null) {
                return parsedApi;
              }
            }
          }
        }
      } catch (e) {
        // Fall through to HTML fallback
      }
    }

    const pageUrl = rawUrl || (source === "myhome"
      ? `https://www.myhome.ge/ka/pr/${sourceId}/`
      : `https://home.ss.ge/ka/udzravi-qoneba/${sourceId}`);
    const res = await fetch(pageUrl, { headers });
    if (!res.ok) return null;
    const html = await res.text();
    const nextMatch = html.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
    const nextData = nextMatch ? JSON.parse(nextMatch[1]) : {};
    const pp = nextData?.props?.pageProps || {};
    const st = pp?.dehydratedState?.queries?.[0]?.state?.data?.data?.statement || pp?.applicationData || pp?.statement || {};
    const agentInfo = pp?.initialAgentInfoData || {};

    if (source === "ss_ge") {
      return parseSsGeStatementDetails(st, agentInfo, html, sourceId, pageUrl);
    }

    const phone9 = extract9DigitPhone(st.user_phone_number || st.phoneNumber || st.phone_number || st.description || "");
    const isAgency = Boolean(st.isForAgency || st.agencyId || st.commercialRealEstateType);
    return {
      id: `${source}_${sourceId}`,
      source,
      source_id: String(sourceId),
      url: pageUrl,
      price_usd: Number(st.price?.priceUsd || st.price_usd || st.total_price || 0),
      area_m2: Number(st.totalArea || st.area || 0),
      floor: String(st.floor || ""),
      rooms: Number(st.rooms || 0),
      district: st.address?.districtTitle || st.district_name || "",
      is_owner: isAgency ? 0 : (st.isOwner === true ? 1 : null),
      user_type: isAgency ? "agency" : (st.isOwner === true ? "physical" : null),
      phone_number: phone9 ? formatGeorgianPhone(phone9) : null,
      author_id: st.userId || st.user_id ? String(st.userId || st.user_id) : null,
      author_listings_count: Number(st.userApplicationCount || st.user_statements_count || 0) || null
    };
  } catch (e) {
    return null;
  }
}

function evaluateWorkerBrokerVerdict(target, relatedRows) {
  const allRows = [];
  const seen = new Set();
  if (target && target.id) {
    allRows.push(target);
    seen.add(target.id);
  }
  for (const r of relatedRows || []) {
    if (r && r.id && !seen.has(r.id)) {
      allRows.push(r);
      seen.add(r.id);
    }
  }

  const aptKeys = new Set();
  let maxAuthorCount = 0;
  let anyNonOwner = false;
  let anyAgentType = false;
  let anyVerifiedOwner = false;
  const agentTokens = ["agent", "agency", "broker", "developer", "company", "აგენტ", "მაკლერ", "სააგენტო"];

  for (const r of allRows) {
    if (r.is_owner === 0 || r.is_owner === false) {
      anyNonOwner = true;
    } else if (r.is_owner === 1 || r.is_owner === true) {
      anyVerifiedOwner = true;
    }
    const ut = String(r.user_type || "").toLowerCase();
    if (agentTokens.some(tok => ut.includes(tok))) anyAgentType = true;
    const cnt = parseInt(r.author_listings_count, 10);
    if (!isNaN(cnt) && cnt > maxAuthorCount) maxAuthorCount = cnt;
    const dist = String(r.district || "").trim();
    const areaBucket = Math.round((Number(r.area_m2) || 0) / 2);
    if (dist || areaBucket) aptKeys.add(`${dist}_${areaBucket}`);
  }

  const uniqueApartments = Math.max(aptKeys.size, allRows.length > 0 && aptKeys.size === 0 ? allRows.length : aptKeys.size);
  const isBroker = anyNonOwner || anyAgentType || maxAuthorCount >= 3 || uniqueApartments >= 2;

  if (isBroker) {
    const reasons = [];
    if (uniqueApartments >= 2 || allRows.length >= 2) {
      reasons.push(`ბაზაში ამ ნომრით/ავტორით იძებნება <b>${allRows.length} განცხადება</b> (${uniqueApartments} სხვადასხვა ბინა)`);
    } else if (maxAuthorCount >= 3) {
      reasons.push(`პორტალზე ავტორს უფიქსირდება <b>${maxAuthorCount} განცხადება</b>`);
    }
    if (anyAgentType || anyNonOwner) {
      reasons.push("პროფილი მონიშნულია როგორც სააგენტო/მაკლერი");
    }
    return {
      isBroker: true,
      badge: "🕵️‍♂️ სავარაუდო ფარული მაკლერი / სააგენტო",
      reason: reasons.length ? reasons.join(" • ") : "დაფიქსირდა სააგენტოს ნიშნები"
    };
  }

  if (!anyVerifiedOwner) {
    return {
      isBroker: false,
      badge: "⚠️ სტატუსი დაუზუსტებელია (გადაუმოწმებელი)",
      reason: "ბაზაში ფიქსირდება 1 განცხადება, თუმცა პორტალზე მესაკუთრის სტატუსი დადასტურებული არ არის"
    };
  }

  return {
    isBroker: false,
    badge: "✅ რეალური მესაკუთრე (Owner)",
    reason: "ბაზაში და პორტალზე ფიქსირდება მხოლოდ 1 ბინა (სააგენტოს ნიშნების გარეშე)"
  };
}

function workerFloorsMatch(f1, f2) {
  const a = String(f1 || "").trim().split("/")[0].trim();
  const b = String(f2 || "").trim().split("/")[0].trim();
  if (!a || !b) return true;
  return a === b;
}

function workerRoomsMatch(r1, r2) {
  const a = parseInt(r1, 10) || 0;
  const b = parseInt(r2, 10) || 0;
  if (a <= 0 || b <= 0) return true;
  return a === b;
}

async function handleXRayQuery(text, env) {
  const parsed = parseXRayInput(text);
  if (!parsed) return null;

  const db = env?.DB;
  if (!db) {
    return "⚠️ D1 ბაზა არ არის დაკავშირებული Cloudflare Worker-თან.";
  }

  try {
    if (parsed.kind === "phone") {
      const { results } = await db
        .prepare("SELECT * FROM properties WHERE phone_number IN (?, ?) ORDER BY scraped_at DESC LIMIT 30")
        .bind(parsed.formattedPhone, parsed.phone9)
        .all();
      const rows = results || [];
      if (rows.length === 0) {
        return (
          `🔎 <b>ტელეფონის რენტგენი:</b> <code>${parsed.formattedPhone}</code>\n\n` +
          `ℹ️ ჩვენს ბაზაში ამ ნომერზე განცხადებები ჯერ არ ფიქსირდება.`
        );
      }

      if (
        rows.length <= 2 &&
        (rows[0].is_owner === null ||
          rows[0].is_owner === undefined ||
          !rows[0].author_id ||
          rows[0].author_listings_count === null ||
          rows[0].author_listings_count === undefined ||
          rows[0].source === "ss_ge")
      ) {
        const live = await fetchLiveStatementDetails(rows[0].source, rows[0].source_id, rows[0].url);
        if (live) {
          rows[0] = mergeLiveIntoRow(rows[0], live);
          await persistLiveEnrichment(db, rows[0].id, rows[0]);
        }
      }

      if (rows[0].author_id) {
        const { results: authorRows } = await db
          .prepare("SELECT * FROM properties WHERE author_id = ? ORDER BY scraped_at DESC LIMIT 30")
          .bind(String(rows[0].author_id))
          .all();
        const seenIds = new Set(rows.map(r => r.id));
        for (const ar of authorRows || []) {
          if (ar && ar.id && !seenIds.has(ar.id)) {
            rows.push(ar);
            seenIds.add(ar.id);
          }
        }
      }

      const verdict = evaluateWorkerBrokerVerdict(rows[0], rows);
      const lines = [
        `🔎 <b>ტელეფონის რენტგენი:</b> <code>${parsed.formattedPhone}</code>`,
        `👤 <b>სტატუსი:</b> ${verdict.badge}`,
        `📌 ${verdict.reason}`,
        "",
        `📋 <b>ნაპოვნი განცხადებები (${rows.length}):</b>`
      ];
      for (const r of rows.slice(0, 5)) {
        const p = Math.round(Number(r.price_usd) || 0).toLocaleString();
        const area = Number(r.area_m2) || 0;
        const dist = r.district || "უცნობი უბანი";
        const src = r.source === "myhome" ? "MyHome" : "SS.ge";
        lines.push(`• <a href="${r.url || "#"}">${src}</a> — <b>$${p}</b> | ${area} მ² | ${dist}`);
      }
      return lines.join("\n");
    }

    let target = null;
    if (parsed.kind === "url") {
      const row = await db
        .prepare("SELECT * FROM properties WHERE id = ?")
        .bind(parsed.listingId)
        .first();
      if (row) {
        target = row;
      }
      if (
        !target ||
        target.is_owner === null ||
        target.is_owner === undefined ||
        !target.phone_number ||
        !target.author_id ||
        target.author_listings_count === null ||
        target.author_listings_count === undefined ||
        target.source === "ss_ge"
      ) {
        const live = await fetchLiveStatementDetails(parsed.source, parsed.sourceId, parsed.url || target?.url);
        if (live) {
          target = target ? mergeLiveIntoRow(target, live) : live;
          if (row && target.id) {
            await persistLiveEnrichment(db, target.id, target);
          }
        }
      }
    } else if (parsed.kind === "fb_text") {
      target = {
        id: "fb_input",
        source: "facebook",
        price_usd: parsed.price_usd || 0,
        area_m2: parsed.area_m2 || 0,
        district: parsed.district || "",
        floor: parsed.floor || "",
        rooms: parsed.rooms || 0,
        phone_number: parsed.formattedPhone,
        is_owner: null,
        user_type: null
      };
    }

    if (!target) {
      return (
        "🔎 <b>განცხადების რენტგენი</b>\n\n" +
        "ℹ️ ეს განცხადება ჩვენს ბაზაში ჯერ არ იძებნება და პორტალიდან მონაცემების წამოღება ვერ მოხერხდა."
      );
    }

    const phone9 = parsed.phone9 || extract9DigitPhone(target.phone_number || "");
    const relatedMap = new Map();
    if (phone9) {
      const fmt = formatGeorgianPhone(phone9);
      const { results } = await db
        .prepare("SELECT * FROM properties WHERE phone_number IN (?, ?) ORDER BY scraped_at DESC LIMIT 30")
        .bind(fmt, phone9)
        .all();
      for (const r of results || []) relatedMap.set(r.id, r);
    }
    if (target.author_id) {
      const { results } = await db
        .prepare("SELECT * FROM properties WHERE author_id = ? ORDER BY scraped_at DESC LIMIT 30")
        .bind(String(target.author_id))
        .all();
      for (const r of results || []) relatedMap.set(r.id, r);
    }

    const verdict = evaluateWorkerBrokerVerdict(target, Array.from(relatedMap.values()));

    // Find exact duplicates
    const dupMap = new Map();
    if (target.group_id) {
      const { results } = await db
        .prepare("SELECT * FROM properties WHERE group_id = ? AND id != ?")
        .bind(target.group_id, target.id || "")
        .all();
      for (const r of results || []) dupMap.set(r.id, r);
    }
    if (target.district && Number(target.area_m2) > 0) {
      const area = Number(target.area_m2);
      const { results } = await db
        .prepare("SELECT * FROM properties WHERE district = ? AND area_m2 BETWEEN ? AND ? AND id != ? ORDER BY price_usd ASC LIMIT 25")
        .bind(target.district, area - 1.5, area + 1.5, target.id || "")
        .all();
      for (const r of results || []) {
        if (!workerFloorsMatch(target.floor, r.floor)) continue;
        if (!workerRoomsMatch(target.rooms, r.rooms)) continue;
        dupMap.set(r.id, r);
      }
    }
    const duplicates = Array.from(dupMap.values()).sort((a, b) => (Number(a.price_usd) || 0) - (Number(b.price_usd) || 0));

    // District benchmark
    let bench = null;
    if (target.district) {
      const bRow = await db
        .prepare("SELECT ROUND(AVG(price_per_m2)) AS avg_m2, COUNT(*) AS cnt FROM properties WHERE district = ? AND price_per_m2 > 300 AND price_per_m2 < 6000")
        .bind(target.district)
        .first();
      if (bRow && bRow.avg_m2 && Number(bRow.cnt) >= 1) {
        bench = { avg_m2: Math.round(Number(bRow.avg_m2)), count: Number(bRow.cnt) };
      }
    }

    const currPrice = Number(target.price_usd) || 0;
    const initPrice = Number(target.initial_price_usd) || currPrice;
    const areaM2 = Number(target.area_m2) || 0;
    const priceM2 = currPrice > 0 && areaM2 > 0 ? Math.round(currPrice / areaM2) : 0;

    const headerTitle = parsed.kind === "fb_text"
      ? "🔎 <b>Facebook განცხადების რენტგენი</b>"
      : "🔎 <b>განცხადების რენტგენი (X-Ray)</b>";
    const lines = [
      headerTitle,
      `👤 <b>გამყიდველი:</b> ${verdict.badge}`,
      `📌 ${verdict.reason}`
    ];

    if (currPrice > 0) {
      lines.push("");
      lines.push(`💰 <b>მიმდინარე ფასი:</b> $${Math.round(currPrice).toLocaleString()}${priceM2 ? ` ($${priceM2.toLocaleString()}/მ²)` : ""}`);
      if (initPrice > 0) {
        lines.push(`📉 პირვანდელი ფასი: <b>$${Math.round(initPrice).toLocaleString()}</b>`);
        const diff = Math.round(currPrice - initPrice);
        if (diff < 0) {
          const pct = Math.round((Math.abs(diff) / initPrice) * 1000) / 10;
          lines.push(`✅ დაკლებულია: <b>-$${Math.abs(diff).toLocaleString()} (-${pct}%)</b>`);
        } else if (diff > 0) {
          const pct = Math.round((diff / initPrice) * 1000) / 10;
          lines.push(`📈 მომატებულია: <b>+$${diff.toLocaleString()} (+${pct}%)</b>`);
        } else {
          lines.push("➖ ფასი არ შეცვლილა (პირვანდელი ფასია)");
        }
      }
    }

    if (bench && priceM2 > 0) {
      const diffPct = Math.round(((priceM2 - bench.avg_m2) / bench.avg_m2) * 100);
      const sign = diffPct > 0 ? `+${diffPct}%` : `${diffPct}%`;
      lines.push(`📊 <b>უბნის საშუალო ფასი (${target.district}):</b> $${bench.avg_m2.toLocaleString()}/მ² (${sign} საბაზროსთან)`);
    }

    lines.push("");
    if (duplicates.length > 0) {
      lines.push("🎯 <b>იგივე ბინა სხვაგან:</b>");
      for (const dup of duplicates.slice(0, 4)) {
        const dupPrice = Math.round(Number(dup.price_usd) || 0);
        const dupSrc = dup.source === "myhome" ? "MyHome.ge" : "SS.ge";
        const dupOwner = dup.is_owner ? "მესაკუთრე ✅" : "სააგენტო 🕵️‍♂️";
        let diffStr = "";
        if (currPrice > 0 && dupPrice > 0) {
          const delta = Math.round(dupPrice - currPrice);
          if (delta < 0) diffStr = ` (<b>-$${Math.abs(delta).toLocaleString()} უფრო იაფად!</b>)`;
          else if (delta > 0) diffStr = ` (+$${delta.toLocaleString()} უფრო ძვირად)`;
          else diffStr = " (იგივე ფასი)";
        }
        lines.push(`• <a href="${dup.url || "#"}">${dupSrc}</a> — <b>$${dupPrice.toLocaleString()}</b>${diffStr} | ${dupOwner}`);
      }
    } else {
      lines.push("🎯 <b>იგივე ბინა სხვაგან:</b> დუბლიკატი სხვა პორტალზე არ იძებნება.");
    }

    return lines.join("\n");
  } catch (err) {
    console.error("X-Ray query error:", err);
    return null;
  }
}
