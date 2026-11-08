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
      if (!isSyncKeyValid(request, env)) return unauthorizedResponse();
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

    // 2.2 Protected X-Ray follow-up from the MyHome lookup job (ADR 0004): POST /api/xray/followup
    if (url.pathname === "/api/xray/followup" && request.method === "POST") {
      return handleXRayFollowup(request, env);
    }

    // Debug status endpoint
    if (url.pathname === "/api/debug") {
      if (!isSyncKeyValid(request, env)) return unauthorizedResponse();
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
  },

  async scheduled(event, env, ctx) {
    const ghToken = env && (env.GITHUB_PAT || env.GITHUB_TOKEN);
    if (!ghToken) return;
    const repo = (env && env.GITHUB_REPO) || "sandrikkk/real-estate";
    const workflow = (env && env.GITHUB_WORKFLOW) || "scrape.yml";
    const ref = (env && env.GITHUB_REF) || "master";
    const dispatchUrl = `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`;
    const task = fetch(dispatchUrl, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${ghToken}`,
        "Accept": "application/vnd.github+json",
        "User-Agent": "tbilisi-real-estate-worker",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ ref })
    }).catch(err => console.error("GitHub workflow_dispatch error:", err));
    if (ctx && typeof ctx.waitUntil === "function") {
      ctx.waitUntil(task);
    } else {
      await task;
    }
  }
};

const PROXY_ALLOWED_HOSTS = [
  "myhome.ge",
  "www.myhome.ge",
  "api-statements.tnet.ge",
  "ss.ge",
  "home.ss.ge",
  "api-gateway.ss.ge",
  "area.ge"
];

// Constant-time compare, so response timing does not reveal how much of a secret matched.
function timingSafeEqualStr(a, b) {
  const x = new TextEncoder().encode(String(a));
  const y = new TextEncoder().encode(String(b));
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    diff |= (x[i] || 0) ^ (y[i] || 0);
  }
  return diff === 0;
}

function isSyncKeyValid(request, env) {
  const expectedKey = (env.SYNC_KEY || "").trim();
  if (!expectedKey) return false;
  const syncKey = (request.headers.get("X-Sync-Key") || new URL(request.url).searchParams.get("key") || "").trim();
  return timingSafeEqualStr(syncKey, expectedKey);
}

function unauthorizedResponse() {
  return new Response(JSON.stringify({ error: "Unauthorized" }), {
    status: 401,
    headers: { "content-type": "application/json" }
  });
}

/**
 * Handles proxying HTML requests (bypasses Cloudflare on target sites)
 */
async function handleProxy(targetUrl, request) {
  try {
    const parsed = new URL(targetUrl);
    if (parsed.protocol !== "https:" || !PROXY_ALLOWED_HOSTS.includes(parsed.hostname)) {
      return new Response(JSON.stringify({ error: "Forbidden target host" }), {
        status: 403,
        headers: { "content-type": "application/json" }
      });
    }
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
    if (!isSyncKeyValid(request, env)) return unauthorizedResponse();

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
    return new Response(JSON.stringify({ error: fatalErr.message || "Internal Server Error" }), {
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
    if (!isSyncKeyValid(request, env)) return unauthorizedResponse();

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
  if (env && env.TELEGRAM_WEBHOOK_SECRET) {
    const providedSecret = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
    if (!timingSafeEqualStr(providedSecret, String(env.TELEGRAM_WEBHOOK_SECRET))) {
      return unauthorizedResponse();
    }
  }

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
      await handleCallbackQuery(update.callback_query, token, kv, env);
      return new Response("OK");
    }

    // 2. Handle Text Messages
    if (update.message && (update.message.text || update.message.caption || update.message.photo)) {
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
  // A forwarded post with a photo carries its text in the caption.
  const text = (msg.text || msg.caption || "").trim();
  // Photos of a post (ADR 0005): stored, and one comparison job per album. A photo without a
  // caption has nothing else to do; a captioned one goes on to the X-Ray of its text.
  if (Array.isArray(msg.photo) && msg.photo.length > 0) {
    await rememberPhoto(env, token, msg, text);
    if (!text) return;
  }
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

  // "მეპატრონე": the owner search for the last flat this chat X-Rayed (ADR 0003 stage 2)
  if (OWNER_WORDS.test(text)) {
    const targetRow = await latestOwnerTarget(env, chatId);
    if (targetRow && isEmptyOwnerTarget(targetRow)) {
      await sendTelegram(token, "sendMessage", {
        chat_id: chatId,
        text: "ℹ️ ბოლო განცხადებიდან ბინა ვერ ამოვიცანი (უბანი და ოთახი/სართული/ფართი არ წერია), ამიტომ მესაკუთრეს ვერ ვეძებ. ჩამიგდე პოსტი, სადაც უბანი წერია."
      });
    } else if (targetRow) {
      await startOwnerSearch(env, token, chatId, targetRow);
    } else {
      await sendTelegram(token, "sendMessage", {
        chat_id: chatId,
        text: "ჯერ ჩამიგდე ბინის პოსტი, ბმული ან ნომერი, მერე მომწერე „მეპატრონე“."
      });
    }
    return;
  }

  // On-Demand X-Ray for MyHome/SS.ge URLs, Georgian phone numbers, or Facebook apartment texts
  const collect = {};
  const xrayReply = await handleXRayQuery(text, env, { chatId, collect });
  if (xrayReply) {
    const targetId = await rememberOwnerTarget(env, chatId, collect.ownerTarget, text);
    const resultId = await saveXRayResult(env, chatId, "dup", collect.resultTarget, collect.duplicates, targetId);
    const markup = xrayReplyMarkup(targetId, resultId, collect.duplicates || [], []);
    await sendTelegram(token, "sendMessage", {
      chat_id: chatId,
      text: xrayReply,
      parse_mode: "HTML",
      disable_web_page_preview: true,
      ...(markup ? { reply_markup: markup } : {})
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
async function handleCallbackQuery(cb, token, kv, env) {
  const chatId = cb.message.chat.id;
  const messageId = cb.message.message_id;
  const data = cb.data;
  const userKey = `USER_${chatId}`;
  let user = (await kv.get(userKey, "json")) || { ...DEFAULT_USER_PROFILE, chat_id: chatId };

  // Answer callback to remove loading state in Telegram client
  await sendTelegram(token, "answerCallbackQuery", { callback_query_id: cb.id });

  // Owner search buttons (ADR 0003 stage 2): only the chat that got the button can use it.
  if (typeof data === "string" && data.startsWith("own:")) {
    let targetRow = null;
    try {
      targetRow = env && env.DB
        ? await env.DB.prepare("SELECT * FROM xray_targets WHERE id = ? AND chat_id = ?").bind(data.slice(4), String(chatId)).first()
        : null;
    } catch (err) {}
    if (targetRow) await startOwnerSearch(env, token, chatId, targetRow);
    return;
  }
  if (typeof data === "string" && data.startsWith("fb:")) {
    await recordOwnerFeedback(env, token, cb, data);
    return;
  }
  if (typeof data === "string" && data.startsWith("fd:")) {
    await recordResultFeedback(env, token, cb, data);
    return;
  }

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

const XRAY_DISTRICT_ALIASES = {
  "საბურთალო": ["საბურთალო", "ვაკე-საბურთალო", "ნუცუბიძე", "ნუცუბიძის ფერდობი", "ვაშლიჯვარი", "ვეძისი", "დელისი", "ლისი", "ლისის მიმდებარედ", "თხინვალი"],
  "ვაკე": ["ვაკე", "ვაკე-საბურთალო", "ბაგები", "კუს ტბა", "ვერა", "წყნეთი"],
  "დიდი დიღომი": ["დიდი დიღომი", "დიღომი", "დიღომი 1-9", "სოფ. დიღომი", "სოფელი დიღომი", "დიღმის ჭალა", "ქოშიგორა", "ვაკე-საბურთალო"],
  "დიღმის მასივი": ["დიღმის მასივი", "დიღმის მას.", "დიდუბე-ჩუღურეთი"],
  "დიღომი": ["დიღომი", "დიდი დიღომი", "დიღომი 1-9", "სოფ. დიღომი", "სოფელი დიღომი", "დიღმის ჭალა", "ქოშიგორა", "ვაკე-საბურთალო"],
  "დიდუბე": ["დიდუბე", "წერეთელი", "წერეთლის გამზ.", "დიდუბე-ჩუღურეთი"],
  "ჩუღურეთი": ["ჩუღურეთი", "კუკია", "სვანეთის უბანი", "ივერთუბანი", "მარჯანიშვილი", "ვორონცოვი", "დიდუბე-ჩუღურეთი"],
  "ნაძალადევი": ["ნაძალადევი", "სანზონა", "სან. ზონა", "სან.ზონა", "თემქა", "ლოტკინი", "გლდანი-ნაძალადევი"],
  "გლდანი": ["გლდანი", "მუხიანი", "გლდანულა", "ავჭალა", "ზაჰესი", "თბილისის ზღვა", "კონიაკის დასახლება", "კონიაკის დას.", "გლდანი-ნაძალადევი"],
  "ისანი": ["ისანი", "ავლაბარი", "ნავთლუღი", "მეტრომშენი", "300 არაგველი", "ისანი-სამგორი"],
  "სამგორი": ["სამგორი", "ვარკეთილი", "ვაზისუბანი", "მესამე მასივი", "მოსკოვის გამზირი", "მოსკოვის გამზ.", "აფრიკა", "ლილო", "ორხევი", "ფონიჭალა", "ისანი-სამგორი"],
  "მთაწმინდა": ["მთაწმინდა", "სოლოლაკი", "რუსთაველი", "თავისუფლება", "ვერა", "ძველი თბილისი"],
  "კრწანისი": ["კრწანისი", "ორთაჭალა", "აბანოთუბანი", "ელია", "ფონიჭალა", "ძველი თბილისი"],
  "ნუცუბიძე": ["ნუცუბიძე", "ნუცუბიძის ფერდობი", "საბურთალო", "ვაკე-საბურთალო"],
  "ნუცუბიძის ფერდობი": ["ნუცუბიძის ფერდობი", "ნუცუბიძე", "საბურთალო", "ვაკე-საბურთალო"],
  "ვაშლიჯვარი": ["ვაშლიჯვარი", "საბურთალო", "ვაკე-საბურთალო"],
  "ლისი": ["ლისი", "ლისის მიმდებარედ", "საბურთალო", "ვაკე-საბურთალო"],
  "ბაგები": ["ბაგები", "ვაკე", "ვაკე-საბურთალო"],
  "ვარკეთილი": ["ვარკეთილი", "სამგორი", "ისანი-სამგორი"],
  "ვაზისუბანი": ["ვაზისუბანი", "სამგორი", "ისანი-სამგორი"],
  "მუხიანი": ["მუხიანი", "გლდანი", "გლდანი-ნაძალადევი"],
  "თემქა": ["თემქა", "ნაძალადევი", "გლდანი-ნაძალადევი"],
  "სანზონა": ["სანზონა", "სან. ზონა", "სან.ზონა", "ნაძალადევი", "გლდანი-ნაძალადევი"],
  "სოლოლაკი": ["სოლოლაკი", "მთაწმინდა", "ძველი თბილისი"],
  "ორთაჭალა": ["ორთაჭალა", "კრწანისი", "ძველი თბილისი"],
  "ავლაბარი": ["ავლაბარი", "ისანი", "ძველი თბილისი", "ისანი-სამგორი"],
  "წერეთელი": ["წერეთელი", "წერეთლის გამზ.", "დიდუბე", "დიდუბე-ჩუღურეთი"]
};

function expandDistrictAliases(district) {
  const d = String(district || "").trim();
  if (!d) return [];
  const aliases = XRAY_DISTRICT_ALIASES[d];
  return aliases ? Array.from(new Set([d, ...aliases])) : [d];
}

// MyHome's own price statistics (the myhome.ge/binebis-pasebi page): average $/m² per area.
// The X-Ray district benchmark comes from here, never from the listings this bot happened to scrape.
const MYHOME_DISTRICT_PRICES_URL = "https://api-statements.tnet.ge/v1/statements/statistics/city-district-urban-prices";
const MYHOME_PRICES_CACHE_SECONDS = 6 * 60 * 60; // MyHome refreshes the figures monthly

// MyHome bedroom_type_id: 1, 2, 3 bedrooms and 6 = studio. 4+ bedrooms have no table.
function workerBedroomTypeFor(rooms, bedrooms) {
  const b = parseInt(bedrooms, 10) || 0;
  const r = parseInt(rooms, 10) || 0;
  if (b >= 1 && b <= 3) return b;
  if (!b) {
    if (r === 1) return 6;
    if (r >= 2 && r <= 4) return r - 1; // rooms = bedrooms + 1
  }
  return null;
}

function myHomeDistrictPricesUrl(bedroomTypeId) {
  const params = ["city_ids[]=1", "deal_type_id=1", "currency_id=2", "square_price=1"];
  if (bedroomTypeId) params.push(`bedroom_type_id=${bedroomTypeId}`);
  return `${MYHOME_DISTRICT_PRICES_URL}?${params.join("&")}`;
}

// data.items = [{ "<group name>": price, "<urban_id>": { "<urban name>": price }, ... }, ...]
// flattened to { name: price } for groups and urbans alike; null when nothing is usable.
function parseMyHomePriceTable(payload) {
  if (!payload || payload.result !== true) return null;
  const items = payload.data && payload.data.items;
  if (!Array.isArray(items)) return null;
  const table = {};
  let count = 0;
  for (const item of items) {
    if (!item || typeof item !== "object") continue;
    for (const [key, value] of Object.entries(item)) {
      if (value && typeof value === "object") {
        for (const [name, price] of Object.entries(value)) {
          if (typeof price === "number") { table[name] = price; count++; }
        }
      } else if (typeof value === "number") {
        table[key] = value;
        count++;
      }
    }
  }
  return count ? table : null;
}

// First of `names` MyHome has a figure for; the caller orders them most specific first.
function lookupMyHomePrice(table, names) {
  if (!table) return null;
  for (const name of names) {
    if (Object.prototype.hasOwnProperty.call(table, name)) return table[name];
  }
  return null;
}

async function fetchMyHomeDistrictPrices(bedroomTypeId) {
  try {
    const res = await fetch(myHomeDistrictPricesUrl(bedroomTypeId), {
      headers: {
        "Accept": "application/json, text/plain, */*",
        "X-Website-Key": "myhome",
        "locale": "ka",
        "Origin": "https://www.myhome.ge",
        "Referer": "https://www.myhome.ge/"
      },
      // Cache good answers at the edge; never pin an error for hours.
      cf: { cacheEverything: true, cacheTtlByStatus: { "200-299": MYHOME_PRICES_CACHE_SECONDS, "300-599": 0 } }
    });
    if (!res.ok) return null;
    return parseMyHomePriceTable(await res.json());
  } catch (err) {
    console.error("MyHome price statistics error:", err);
    return null;
  }
}

function formatOwnerBadge(isOwner) {
  if (isOwner === true || isOwner === 1 || isOwner === "1") return "მესაკუთრე ✅";
  if (isOwner === false || isOwner === 0 || isOwner === "0") return "სააგენტო 🕵️‍♂️";
  return "გადაუმოწმებელი ⚠️";
}

function formatGeorgianPhone(phone9) {
  return `+995 ${phone9.slice(0, 3)} ${phone9.slice(3, 5)} ${phone9.slice(5, 7)} ${phone9.slice(7, 9)}`;
}

function extract9DigitPhone(text) {
  if (!text) return null;
  const cleaned = String(text).replace(/https?:\/\/\S+/g, " ");
  const m = cleaned.match(/(?<!\d)(?:\+?995[\s\-.]*)?(5\d{2}(?:[\s\-.]*\d{2}[\s\-.]*\d{2}[\s\-.]*\d{2}|[\s\-.]*\d{3}[\s\-.]*\d{3}|[\s\-.]*\d{6}))(?!\d)/);
  if (!m) return null;
  const digits = m[1].replace(/\D/g, "");
  return (digits.length === 9 && digits.startsWith("5")) ? digits : null;
}

function extractMaskedPhone(text) {
  if (!text) return null;
  const cleaned = String(text).replace(/https?:\/\/\S+/g, " ");
  const m = cleaned.match(/(?:\+?995[\s\-.]*)?(5\d{2}[\s\-.]*\d{2}[\s\-.]*\d)\*{2,4}/);
  if (!m) return null;
  const digits = m[1].replace(/\D/g, "");
  return (digits.length === 6 && digits.startsWith("5")) ? `${digits}***` : null;
}

function romanToInt(s) {
  const val = { I: 1, V: 5, X: 10 };
  let total = 0;
  for (let i = 0; i < s.length; i++) {
    const v = val[s[i]];
    const next = val[s[i + 1]] || 0;
    total += v < next ? -v : v;
  }
  return total;
}

// Micro-district number: "მე-6 მიკრო", "6-ე მიკრორაიონი", "3 მ/რ", and MyHome's "გლდანი - VI მ/რ".
function extractMicroDistrict(text) {
  if (!text) return null;
  const m = String(text).match(
    /(?<![\dA-Za-z\u10A0-\u10FF])(?:მე[-\s]*)?(\d{1,2}|[IVX]{1,5})(?:[-\s]*(?:ე|ლი))?[-\s]*(?:მ\s*\/\s*რ|მ\.\s*რ\.?|მიკრო)/i
  );
  if (!m) return null;
  const n = /^\d+$/.test(m[1]) ? parseInt(m[1], 10) : romanToInt(m[1].toUpperCase());
  return n > 0 ? n : null;
}

// A parsed post carries `micro`; a listing row names its micro-district in subdistrict or street.
function microOf(listing) {
  const own = parseInt(listing?.micro, 10);
  if (own > 0) return own;
  return extractMicroDistrict(`${listing?.subdistrict || ""} ${listing?.street || ""}`);
}

function parseXRayInput(text) {
  if (!text || typeof text !== "string") return null;
  const stripped = text.trim();
  if (!stripped || stripped.startsWith("/")) return null;
  if (/^\d+(?:\.\d+)?\s*[-\s]\s*\d+(?:\.\d+)?$/.test(stripped) && !/^5\d{2}[\s-]+\d{6}$/.test(stripped)) return null;

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
  const areaMatch = stripped.match(/\b(\d{2,3}(?:[.,]\d+)?)\s*(?:კვ(?:[./]?\s*მ)?(?:²|2)?(?![\u10A0-\u10FFa-zA-Z\u0400-\u04FF])|კვა?დრატ\S*|кв\.?\s*м|მ²|მ2|m2|m²|sqm)/i);
  if (areaMatch) areaM2 = parseFloat(areaMatch[1].replace(",", "."));

  let floor = null;
  const floorWordMap = { "პირველ": "1", "მეორე": "2" };
  const floorMatch = stripped.match(/(?:(?:მე[- \t]*)?([1-9]\d?)(?:[- \t]*(?:ე|ლი))?(?:[ \t]*\/[ \t]*\d{1,2})?|(?<![\u10A0-\u10FF])(პირველ|მეორე)ი?)[ \t]+სართულ(?:ი|ზე)?(?![\u10A0-\u10FF])|(?:მე[- \t]*)?([1-9]\d?)[ \t]*\/[ \t]*\d{1,2}[ \t]*სართულ\S*|სართულ\S*[ \t]*[:\-–—]?[ \t]*([1-9]\d?)(?:[ \t]*\/[ \t]*\d{1,2})?(?![ \t]*(?:კვ|მ²|მ2|m2|m²|sqm)|\d)/i);
  if (floorMatch) {
    floor = floorMatch[2] ? floorWordMap[floorMatch[2].toLowerCase()] : (floorMatch[1] || floorMatch[3] || floorMatch[4]);
  } else {
    const slashRe = /(?<![\d/])(?<!\d[.,])(\d{1,2})[ \t]*\/[ \t]*(\d{1,2})(?![\d/]|[.,]\d)/g;
    let sm;
    while ((sm = slashRe.exec(stripped)) !== null) {
      const fVal = parseInt(sm[1], 10);
      const totalVal = parseInt(sm[2], 10);
      const before = stripped.slice(Math.max(0, sm.index - 3), sm.index).toLowerCase();
      const after = stripped.slice(sm.index + sm[0].length, sm.index + sm[0].length + 8).toLowerCase();
      if (fVal === 24 && totalVal === 7) continue;
      if (/[მm]\s*$/.test(before)) continue;
      if (/^\s*წუთ/.test(after)) continue;
      if (fVal >= 1 && fVal <= totalVal && totalVal <= 40 && totalVal >= 2) {
        floor = String(fVal);
        break;
      }
    }
  }

  let rooms = null;
  const roomsMatch = stripped.match(/(?<!\d)(?<!\d[.,])(\d)[ \t-]*ოთახ\S*/i);
  if (roomsMatch) {
    rooms = parseInt(roomsMatch[1], 10);
  } else {
    const roomsWordMatch = stripped.match(/(?<![\u10A0-\u10FF])(ერთ|ორ|სამ|ოთხ|ხუთ)ი?[ \t-]*ოთახ\S*/i);
    if (roomsWordMatch) {
      const roomsWordMap = { "ერთ": 1, "ორ": 2, "სამ": 3, "ოთხ": 4, "ხუთ": 5 };
      rooms = roomsWordMap[roomsWordMatch[1].toLowerCase()];
    }
  }

  // Bedrooms: "2 საძინებელი", "ორი ნათელი საძინებლით" (one word may sit in between)
  let bedrooms = null;
  const bedroomsMatch = stripped.match(/(?<![\d\u10A0-\u10FF])([1-9]|ერთ|ორ|სამ|ოთხ|ხუთ)ი?(?:[ \t-]+[\u10A0-\u10FF]+)?[ \t-]*საძინებ/i);
  if (bedroomsMatch) {
    const bedroomsWordMap = { "ერთ": 1, "ორ": 2, "სამ": 3, "ოთხ": 4, "ხუთ": 5 };
    bedrooms = bedroomsWordMap[bedroomsMatch[1].toLowerCase()] || parseInt(bedroomsMatch[1], 10);
  }

  // Building height: "7/25" (only when 7 is the parsed floor), "5-სართულიან", "(9) სართულიანია"
  let totalFloors = null;
  const slashTotalRe = /(?<![\d/])([1-9]\d?)[ \t]*\/[ \t]*(\d{1,2})(?![\d/])/g;
  let st;
  while (floor && (st = slashTotalRe.exec(stripped)) !== null) {
    if (st[1] === floor) {
      totalFloors = parseInt(st[2], 10);
      break;
    }
  }
  if (totalFloors === null) {
    const heightMatch = stripped.match(/(?<!\d)(?:(\d{1,2})|\((\d{1,2})\))[ \t]*-?[ \t]*სართულიან/);
    if (heightMatch) totalFloors = parseInt(heightMatch[1] || heightMatch[2], 10);
  }
  if (totalFloors !== null && (totalFloors < 2 || totalFloors > 40 || (floor && totalFloors < parseInt(floor, 10)))) {
    totalFloors = null;
  }

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
    if (new RegExp(`(?<![\\u10A0-\\u10FF])${stem}`).test(lower)) {
      district = canonical;
      break;
    }
  }

  const phone9 = extract9DigitPhone(stripped);
  const maskedPhone = phone9 ? `${phone9.slice(0, 6)}***` : extractMaskedPhone(stripped);
  const flat = {
    area_m2: areaM2,
    floor,
    rooms,
    bedrooms,
    total_floors: totalFloors,
    price_usd: priceUsd,
    district,
    micro: extractMicroDistrict(stripped)
  };
  const hasFbSignal = areaM2 !== null && (district !== null || priceUsd !== null || floor !== null || rooms !== null);
  if (hasFbSignal) {
    return {
      kind: "fb_text",
      phone9,
      formattedPhone: phone9 ? formatGeorgianPhone(phone9) : maskedPhone,
      maskedPhone,
      ...flat
    };
  }

  // Without an area the post is not confirmed as a flat listing (it may be a buyer or a house),
  // so what it names is kept apart as a hint for the owner search (ADR 0003), not as parsed facts.
  if (phone9 || maskedPhone) {
    return {
      kind: "phone",
      phone9,
      formattedPhone: phone9 ? formatGeorgianPhone(phone9) : maskedPhone,
      maskedPhone,
      flat
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
    html && />[\s]*მესაკუთრე[\s]*</.test(html)
  );

  const isBrokerSignal = Boolean(
    agencyId ||
    agencyName ||
    st.isForAgency ||
    st.isAgency === true ||
    st.isOwner === false ||
    st.isOwner === 0 ||
    st.isOwner === "0" ||
    st.commercialRealEstateType ||
    st.userType === 2 ||
    agentInfo.companyId != null ||
    infoType === 2 ||
    ["broker", "agent", "agency", "company", "developer", "აგენტ", "მაკლერ", "სააგენტო"].some(k => uet.includes(k)) ||
    appCount >= 3 ||
    htmlHasAgentBadge
  );

  const isVerifiedOwner = !isBrokerSignal && Boolean(
    ["individual", "owner", "მესაკუთრე"].some(k => uet.includes(k)) ||
    st.isOwner === true ||
    st.isOwner === 1 ||
    st.isOwner === "1" ||
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

function isMaskedPhone(phone) {
  return String(phone || "").includes("*");
}

function mergeLiveIntoRow(row, live) {
  if (!live) return row;
  if (!row) return live;
  const merged = { ...row };
  // The live portal answer is the freshest fact, so it replaces the stored flag both ways.
  const liveOwnerKnown = live.is_owner !== null && live.is_owner !== undefined;
  if (liveOwnerKnown) {
    merged.is_owner = live.is_owner === 0 || live.is_owner === false || live.is_owner === "0" ? 0 : 1;
  }
  if (live.user_type && (liveOwnerKnown || !merged.user_type)) {
    merged.user_type = live.user_type;
  }
  if (
    live.phone_number &&
    (!merged.phone_number || (isMaskedPhone(merged.phone_number) && !isMaskedPhone(live.phone_number)))
  ) {
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
  // MyHome answers the Worker with a Cloudflare challenge (403) and its API with 401, so only
  // SS.ge is read live here; MyHome listings are read by the lookup job (ADR 0004).
  if (source !== "ss_ge") return null;
  const headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ka,en-US;q=0.9,en;q=0.8"
  };

  try {
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

    const pageUrl = rawUrl || `https://home.ss.ge/ka/udzravi-qoneba/${sourceId}`;
    const res = await fetch(pageUrl, { headers });
    if (!res.ok) return null;
    const html = await res.text();
    const nextMatch = html.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
    const nextData = nextMatch ? JSON.parse(nextMatch[1]) : {};
    const pp = nextData?.props?.pageProps || {};
    let dehydratedStmt = null;
    const queries = pp?.dehydratedState?.queries;
    if (Array.isArray(queries)) {
      for (const q of queries) {
        const cand = q?.state?.data?.data?.statement || q?.state?.data?.statement;
        if (cand && typeof cand === "object") {
          dehydratedStmt = cand;
          break;
        }
      }
    }
    const st = dehydratedStmt || pp?.applicationData || pp?.statement || {};
    const agentInfo = pp?.initialAgentInfoData || {};

    return parseSsGeStatementDetails(st, agentInfo, html, sourceId, pageUrl);
  } catch (e) {
    return null;
  }
}

// MyHome answers the Worker with a Cloudflare challenge (403), so the live MyHome lookup runs
// in GitHub Actions (scripts/xray_lookup.py, ADR 0004). The Worker queues the job, shows what
// the last finished lookup saved, and sends the job's result as a follow-up message.
const MYHOME_SEARCH_URL = "https://www.myhome.ge/ka/s/udzravi-qoneba/";
const MYHOME_LOOKUP_WORKFLOW = "xray-lookup.yml";
const MYHOME_LOOKUP_FRESH_MINUTES = 30; // a lookup this recent is shown again, not repeated

function myHomeSearchUrl(params) {
  const url = new URL(MYHOME_SEARCH_URL);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, String(v));
  return url.toString();
}

function isFreshIso(iso, minutes) {
  const t = Date.parse(iso || "");
  return Number.isFinite(t) && Date.now() - t <= minutes * 60 * 1000;
}

async function latestMyHomeLookup(db, phone9) {
  try {
    return await db
      .prepare(
        "SELECT * FROM xray_jobs WHERE phone9 = ? AND status IN ('done', 'failed') ORDER BY done_at DESC LIMIT 1"
      )
      .bind(phone9)
      .first();
  } catch (err) {
    return null;
  }
}

// Rows the lookup job saved for this phone; with `since`, only those saved by that job.
async function loadSavedLiveListings(db, phone9, since = null) {
  try {
    const { results } = since
      ? await db
          .prepare("SELECT * FROM xray_live_listings WHERE phone9 = ? AND seen_at >= ? ORDER BY seen_at DESC LIMIT 30")
          .bind(phone9, since)
          .all()
      : await db
          .prepare("SELECT * FROM xray_live_listings WHERE phone9 = ? ORDER BY seen_at DESC LIMIT 30")
          .bind(phone9)
          .all();
    return results || [];
  } catch (err) {
    return [];
  }
}

// The lookup job's row for one MyHome listing (a "listing" job), if it read that listing.
async function loadSavedListing(db, listingId) {
  try {
    return await db.prepare("SELECT * FROM xray_live_listings WHERE id = ?").bind(listingId).first();
  } catch (err) {
    return null;
  }
}

// Records the job and dispatches the workflow. `job` is { kind: "phone", phone9 },
// { kind: "listing", listingId } or { kind: "owner", targetId }. Returns the job id, or null.
async function queueMyHomeLookup(env, chatId, job) {
  const token = env && (env.GITHUB_PAT || env.GITHUB_TOKEN);
  if (!env || !env.DB || !token || !chatId) {
    if (env && !token) console.error("MyHome lookup not queued: the Worker has no GITHUB_PAT secret");
    return null;
  }
  const id = crypto.randomUUID();
  try {
    await env.DB
      .prepare(
        "INSERT INTO xray_jobs (id, kind, phone9, listing_id, target_id, chat_id, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)"
      )
      // "" not null: the first production xray_jobs declared phone9 NOT NULL.
      .bind(id, job.kind, job.phone9 || "", job.listingId || null, job.targetId || null, String(chatId), new Date().toISOString())
      .run();
    const repo = env.GITHUB_REPO || "sandrikkk/real-estate";
    const ref = env.GITHUB_REF || "master";
    const res = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/${MYHOME_LOOKUP_WORKFLOW}/dispatches`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Accept": "application/vnd.github+json",
        "User-Agent": "tbilisi-real-estate-worker",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json"
      },
      // Only the opaque job id: the repository is public, and so are workflow inputs.
      body: JSON.stringify({ ref, inputs: { job_id: id } })
    });
    if (res.status === 204) return id;
    console.error(`MyHome lookup dispatch failed: HTTP ${res.status}`);
  } catch (err) {
    console.error("MyHome lookup queue error:", err);
  }
  return null;
}

// How the phone report covers MyHome: "fresh" (a finished lookup's rows), "queued" (a job
// was started), "failed" (no lookup possible now), or "none" (no full number to look up).
async function myHomeLiveState(env, parsed, chatId) {
  if (!parsed.phone9) return { mode: "none", rows: [], full: false };
  const db = env.DB;
  const last = await latestMyHomeLookup(db, parsed.phone9);
  if (last && last.status === "done" && isFreshIso(last.done_at, MYHOME_LOOKUP_FRESH_MINUTES)) {
    return {
      mode: "fresh",
      rows: await loadSavedLiveListings(db, parsed.phone9, last.created_at),
      full: Boolean(last.page_full)
    };
  }
  const saved = await loadSavedLiveListings(db, parsed.phone9);
  const jobId = await queueMyHomeLookup(env, chatId, { kind: "phone", phone9: parsed.phone9 });
  return { mode: jobId ? "queued" : "failed", rows: saved, full: false };
}

// POST /api/xray/followup {job_id}: called by the lookup job when it has finished.
async function handleXRayFollowup(request, env) {
  if (!isSyncKeyValid(request, env)) return unauthorizedResponse();
  const json = (body, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  try {
    const { job_id: jobId } = await request.json();
    const job = env.DB && jobId
      ? await env.DB.prepare("SELECT * FROM xray_jobs WHERE id = ?").bind(String(jobId)).first()
      : null;
    if (!job) return json({ success: false, error: "job not found" }, 404);
    let text;
    let replyMarkup = null;
    if (job.kind === "owner" || job.kind === "photo") {
      let result = null;
      try {
        result = job.status === "done" && job.result ? JSON.parse(job.result) : null;
      } catch (err) {}
      text = result
        ? result.text
        : job.kind === "photo"
          ? "⚠️ ფოტოების შედარება ვერ მოხერხდა, ცოტა ხანში სცადე ხელახლა."
          : "⚠️ მესაკუთრის ძებნა ვერ მოხერხდა, ცოტა ხანში სცადე ხელახლა.";
      if (result && Array.isArray(result.candidates) && result.candidates.length > 0) {
        replyMarkup = feedbackKeyboard(job.id, result.candidates, []);
      }
    } else if (job.kind === "listing") {
      // The job saved the listing's row, so the X-Ray now reads it without queueing again.
      // No chat id is passed on: that could queue another lookup.
      if (job.status === "done") {
        const collect = {};
        const report = await handleXRayQuery(`https://www.myhome.ge/ka/pr/${job.listing_id}/`, env, { collect });
        text = `🔄 <b>myhome-ის შედეგი</b>\n\n${report}`;
        const resultId = await saveXRayResult(env, job.chat_id, "dup", collect.resultTarget, collect.duplicates, null);
        replyMarkup = xrayReplyMarkup(null, resultId, collect.duplicates || [], []);
      } else {
        text = "⚠️ myhome-ზე განცხადების შემოწმება ვერ მოხერხდა. ზემოთ ნაჩვენებია ჩვენი ბაზის შედეგი.";
      }
    } else if (job.status === "done") {
      const fmt = formatGeorgianPhone(job.phone9);
      const parsed = { kind: "phone", phone9: job.phone9, formattedPhone: fmt, maskedPhone: `${job.phone9.slice(0, 6)}***` };
      const live = {
        mode: "fresh",
        rows: await loadSavedLiveListings(env.DB, job.phone9, job.created_at),
        full: Boolean(job.page_full)
      };
      text = `🔄 <b>myhome-ის შედეგი</b>\n\n${await buildPhoneReport(env.DB, parsed, live)}`;
    } else {
      text = `⚠️ myhome-ზე ძებნა ვერ მოხერხდა (<code>${formatGeorgianPhone(job.phone9)}</code>). ზემოთ ნაჩვენებია ჩვენი ბაზის შედეგი.`;
    }
    await sendTelegram(env.TELEGRAM_BOT_TOKEN, "sendMessage", {
      chat_id: job.chat_id,
      text,
      parse_mode: "HTML",
      disable_web_page_preview: true,
      ...(replyMarkup ? { reply_markup: replyMarkup } : {})
    });
    return json({ success: true });
  } catch (err) {
    return json({ success: false, error: err.message }, 500);
  }
}

// Owner search (ADR 0003 stage 2). An X-Ray reply that names a flat gets a
// "🔍 მესაკუთრის ძებნა" button; the button or the word "მეპატრონე" queues an "owner" job
// (scripts/xray_lookup.py, core/owner_search.py), and its answer comes with ✅/❌ buttons
// whose presses go to xray_feedback, the labels ADR 0002 learns from.
const OWNER_WORDS = /^(?:მეპატრონე|მესაკუთრე)[\s!?.]*$/;
const OWNER_TARGET_FIELDS = [
  "district", "subdistrict", "street", "micro", "rooms", "bedrooms", "floor", "total_floors",
  "area_m2", "price_usd", "deal_type", "phone9", "listing_id", "author_id", "group_id"
];

// The flat an X-Ray saw, or null when too little is known to search for it.
function ownerTarget(fields, text) {
  const t = { ...fields, micro: microOf(fields) };
  if (!t.deal_type) t.deal_type = /ქირავდება|ქირით/.test(String(text || "")) ? "rent" : "sale";
  if (!t.district || !(t.rooms || t.floor || Number(t.area_m2) > 0)) return null;
  const out = {};
  for (const k of OWNER_TARGET_FIELDS) {
    if (t[k] !== null && t[k] !== undefined && t[k] !== "" && t[k] !== 0) out[k] = t[k];
  }
  return out;
}

function ownerTargetSummary(t) {
  const parts = [t.district || "უცნობი უბანი"];
  if (t.micro) parts.push(`მე-${t.micro} მიკრო`);
  if (t.rooms) parts.push(`${t.rooms} ოთახი`);
  const floor = cleanFloorStr(t.floor);
  if (floor) parts.push(`${floor} სართ.`);
  if (Number(t.area_m2) > 0) parts.push(`${Number(t.area_m2)} მ²`);
  return parts.join(", ");
}

async function saveOwnerTarget(env, chatId, target) {
  if (!env || !env.DB || !chatId || !target) return null;
  const id = crypto.randomUUID();
  try {
    await env.DB
      .prepare("INSERT INTO xray_targets (id, chat_id, target, created_at) VALUES (?, ?, ?, ?)")
      .bind(id, String(chatId), JSON.stringify(target), new Date().toISOString())
      .run();
    return id;
  } catch (err) {
    return null;
  }
}

function isEmptyOwnerTarget(row) {
  try {
    return Object.keys(JSON.parse(row.target)).length === 0;
  } catch (err) {
    return true;
  }
}

// What the next "მეპატრონე" searches for. An X-Ray that names a flat saves it (and gets the
// button). One that names none still replaces the chat's earlier flat with an empty one, so
// the word cannot search a flat the user has moved on from, unless the message is about the
// number of that earlier flat. Returns the saved flat's id, or null when there is no button.
async function rememberOwnerTarget(env, chatId, target, text) {
  if (target) return saveOwnerTarget(env, chatId, target);
  const last = await latestOwnerTarget(env, chatId);
  const phone9 = extract9DigitPhone(text);
  let lastPhone9 = null;
  try {
    lastPhone9 = last ? JSON.parse(last.target).phone9 || null : null;
  } catch (err) {}
  if (phone9 && phone9 === lastPhone9) return null;
  await saveOwnerTarget(env, chatId, {});
  return null;
}

function ownerButton(targetId) {
  return { inline_keyboard: [[{ text: "🔍 მესაკუთრის ძებნა", callback_data: `own:${targetId}` }]] };
}

async function startOwnerSearch(env, token, chatId, targetRow) {
  let target = {};
  try {
    target = JSON.parse(targetRow.target);
  } catch (err) {}
  const jobId = await queueMyHomeLookup(env, chatId, { kind: "owner", targetId: targetRow.id });
  await sendTelegram(token, "sendMessage", {
    chat_id: chatId,
    text: jobId
      ? `🔍 <b>მესაკუთრეს ვეძებ:</b> ${ownerTargetSummary(target)}\nmyhome, ss.ge და ჩვენი ბაზა, დაახლოებით 1 წუთში მოგწერ.`
      : "⚠️ მესაკუთრის ძებნა ახლა ვერ დავიწყე, ცოტა ხანში სცადე.",
    parse_mode: "HTML"
  });
}

// Telegram sends an album as one message per photo, sharing media_group_id; the caption sits
// on one of them. One batch is one album, or one single photo.
function photoBatchId(msg) {
  const chatId = String(msg.chat.id);
  return msg.media_group_id ? `${chatId}:${msg.media_group_id}` : `${chatId}:m${msg.message_id}`;
}

async function rememberPhoto(env, token, msg, caption) {
  if (!env || !env.DB) return;
  const chatId = String(msg.chat.id);
  const batchId = photoBatchId(msg);
  const largest = msg.photo[msg.photo.length - 1];
  const now = new Date().toISOString();
  try {
    await env.DB
      .prepare("INSERT OR IGNORE INTO xray_photos (batch_id, message_id, chat_id, file_id, created_at) VALUES (?, ?, ?, ?, ?)")
      .bind(batchId, msg.message_id, chatId, largest.file_id, now)
      .run();
    const created = await env.DB
      .prepare("INSERT OR IGNORE INTO xray_photo_batches (batch_id, chat_id, caption, created_at) VALUES (?, ?, ?, ?)")
      .bind(batchId, chatId, caption || null, now)
      .run();
    if (caption) {
      await env.DB.prepare("UPDATE xray_photo_batches SET caption = ? WHERE batch_id = ?").bind(caption, batchId).run();
    }
    // Only the update that created the batch starts the job: the job reads every photo of it.
    if (!created || !created.meta || created.meta.changes !== 1) return;
    const jobId = await queueMyHomeLookup(env, chatId, { kind: "photo", targetId: batchId });
    await sendTelegram(token, "sendMessage", {
      chat_id: chatId,
      text: jobId
        ? "📸 ფოტოებს ვადარებ ჩვენს ბაზას, დაახლოებით 1 წუთში მოგწერ."
        : "⚠️ ფოტოების შედარება ახლა ვერ დავიწყე, ცოტა ხანში სცადე."
    });
  } catch (err) {
    console.error("X-Ray photo save error:", err && err.message);
  }
}

async function latestOwnerTarget(env, chatId) {
  if (!env || !env.DB) return null;
  try {
    return await env.DB
      .prepare("SELECT * FROM xray_targets WHERE chat_id = ? ORDER BY created_at DESC LIMIT 1")
      .bind(String(chatId))
      .first();
  } catch (err) {
    return null;
  }
}

// One row per candidate; `answers` are the xray_feedback rows already given, shown with ✓.
// `prefix` tells the callback handler which table holds the candidates: "fb" for an owner-search
// job (xray_jobs.result), "fd" for the duplicate lines of an X-Ray reply (xray_results).
function feedbackKeyboard(jobId, candidates, answers, prefix = "fb") {
  const label = new Map((answers || []).map(a => [a.candidate_id, Number(a.label)]));
  return {
    inline_keyboard: candidates.map((c, i) => [
      { text: `${i + 1} ✅ იგივეა${label.get(c.id) === 1 ? " ✓" : ""}`, callback_data: `${prefix}:${jobId}:${i}:y` },
      { text: `${i + 1} ❌ სხვაა${label.get(c.id) === 0 ? " ✓" : ""}`, callback_data: `${prefix}:${jobId}:${i}:n` }
    ])
  };
}

// The target's fields kept with an X-Ray result, so a ✅/❌ answer can be learned from.
function xrayResultTarget(target) {
  const out = {};
  for (const k of [...OWNER_TARGET_FIELDS, "id", "source"]) {
    const v = target && target[k];
    if (v !== null && v !== undefined && v !== "") out[k] = v;
  }
  return out;
}

async function saveXRayResult(env, chatId, kind, target, candidates, ownerTargetId) {
  if (!env || !env.DB || !chatId || !Array.isArray(candidates) || candidates.length === 0) return null;
  const id = crypto.randomUUID();
  try {
    await env.DB
      .prepare("INSERT INTO xray_results (id, chat_id, kind, target, candidates, owner_target_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)")
      .bind(id, String(chatId), kind, JSON.stringify(target || {}), JSON.stringify(candidates), ownerTargetId || null, new Date().toISOString())
      .run();
    return id;
  } catch (err) {
    console.error("X-Ray result save error:", err && err.message);
    return null;
  }
}

// The owner button (if any) on top, then one ✅/❌ row per duplicate line.
function xrayReplyMarkup(ownerTargetId, resultId, candidates, answers) {
  const rows = [];
  if (ownerTargetId) rows.push(...ownerButton(ownerTargetId).inline_keyboard);
  if (resultId) rows.push(...feedbackKeyboard(resultId, candidates, answers, "fd").inline_keyboard);
  return rows.length ? { inline_keyboard: rows } : null;
}

// "fb:<job id>:<candidate index>:<y|n>"; a later press replaces an earlier one.
async function recordOwnerFeedback(env, token, cb, data) {
  const [, jobId, idxText, answer] = String(data).split(":");
  const chatId = String(cb.message.chat.id);
  if (!env || !env.DB || (answer !== "y" && answer !== "n")) return;
  try {
    const job = await env.DB
      .prepare("SELECT * FROM xray_jobs WHERE id = ? AND chat_id = ?")
      .bind(jobId, chatId)
      .first();
    const candidates = job && job.result ? JSON.parse(job.result).candidates || [] : [];
    const cand = candidates[parseInt(idxText, 10)];
    if (!cand) return;
    await env.DB
      .prepare(
        "INSERT OR REPLACE INTO xray_feedback (job_id, candidate_id, chat_id, target_id, label, created_at) VALUES (?, ?, ?, ?, ?, ?)"
      )
      .bind(jobId, cand.id, chatId, job.target_id || null, answer === "y" ? 1 : 0, new Date().toISOString())
      .run();
    const { results } = await env.DB
      .prepare("SELECT candidate_id, label FROM xray_feedback WHERE job_id = ? AND chat_id = ?")
      .bind(jobId, chatId)
      .all();
    await sendTelegram(token, "editMessageReplyMarkup", {
      chat_id: cb.message.chat.id,
      message_id: cb.message.message_id,
      reply_markup: feedbackKeyboard(jobId, candidates, results)
    });
  } catch (err) {
    console.error("X-Ray feedback error:", err && err.message);
  }
}

// "fd:<result id>:<candidate index>:<y|n>": the same answer as recordOwnerFeedback, for the
// duplicate lines of an X-Ray reply. The candidates sit in xray_results, and the owner button
// of that reply stays under the updated rows.
async function recordResultFeedback(env, token, cb, data) {
  const [, resultId, idxText, answer] = String(data).split(":");
  const chatId = String(cb.message.chat.id);
  if (!env || !env.DB || (answer !== "y" && answer !== "n")) return;
  try {
    const row = await env.DB
      .prepare("SELECT * FROM xray_results WHERE id = ? AND chat_id = ?")
      .bind(resultId, chatId)
      .first();
    const candidates = row ? JSON.parse(row.candidates || "[]") : [];
    const cand = candidates[parseInt(idxText, 10)];
    if (!cand) return;
    await env.DB
      .prepare(
        "INSERT OR REPLACE INTO xray_feedback (job_id, candidate_id, chat_id, target_id, label, created_at) VALUES (?, ?, ?, ?, ?, ?)"
      )
      .bind(resultId, cand.id, chatId, null, answer === "y" ? 1 : 0, new Date().toISOString())
      .run();
    const { results } = await env.DB
      .prepare("SELECT candidate_id, label FROM xray_feedback WHERE job_id = ? AND chat_id = ?")
      .bind(resultId, chatId)
      .all();
    await sendTelegram(token, "editMessageReplyMarkup", {
      chat_id: cb.message.chat.id,
      message_id: cb.message.message_id,
      reply_markup: xrayReplyMarkup(row.owner_target_id, resultId, candidates, results)
    });
  } catch (err) {
    console.error("X-Ray result feedback error:", err && err.message);
  }
}

// `where` names the sources the rows came from, for the count in the reason line.
function evaluateWorkerBrokerVerdict(target, relatedRows, where = "ბაზაში") {
  const allRows = [];
  const seen = new Set();
  const isPhonelessFb = Boolean(
    target && target.id === "fb_input" && !target.phone_number && !target.author_id
  );
  if (target && target.id && !isPhonelessFb) {
    allRows.push(target);
    seen.add(target.id);
  }
  for (const r of relatedRows || []) {
    if (r && r.id && !seen.has(r.id)) {
      allRows.push(r);
      seen.add(r.id);
    }
  }

  if (isPhonelessFb && allRows.length === 0) {
    return {
      isBroker: false,
      badge: "⚠️ სტატუსი დაუზუსტებელია (ნომერი არ არის მითითებული)",
      reason: "განცხადებაში ტელეფონის ნომერი არ წერია და ბაზაში იდენტური ბინა ჯერ არ იძებნება"
    };
  }

  const clusters = [];
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

    const rGid = r.group_id || null;
    const rDist = String(r.district || "").trim();
    const rAliases = new Set(rDist ? expandDistrictAliases(rDist) : []);
    const rFloor = cleanFloorStr(r.floor);
    const rArea = Number(r.area_m2) || 0;
    const rRooms = parseInt(r.rooms, 10) || 0;

    let matchedCluster = false;
    for (const c of clusters) {
      if (rGid && c.group_id && rGid === c.group_id) {
        matchedCluster = true;
        break;
      }
      const distCompat = !rDist || !c.district || c.aliases.has(rDist) || rAliases.has(c.district);
      const areaCompat = rArea > 0 && c.area_m2 > 0 && Math.abs(rArea - c.area_m2) <= 2.0;
      const floorCompat = !rFloor || !c.floor || rFloor === c.floor;
      const roomsCompat = rRooms <= 0 || c.rooms <= 0 || rRooms === c.rooms;
      if (distCompat && areaCompat && floorCompat && roomsCompat) {
        matchedCluster = true;
        break;
      }
    }
    if (!matchedCluster) {
      clusters.push({
        group_id: rGid,
        district: rDist,
        aliases: rAliases,
        area_m2: rArea,
        floor: rFloor,
        rooms: rRooms
      });
    }
  }

  const uniqueApartments = clusters.length;
  const isBroker = anyNonOwner || anyAgentType || maxAuthorCount >= 3 || uniqueApartments >= 2;
  // A Facebook post is a flat the seller offers, not a listing found under the number.
  const postIsRow = allRows.length > 0 && allRows[0].id === "fb_input";

  if (isBroker) {
    const reasons = [];
    if (postIsRow && allRows.length >= 2) {
      const flats = uniqueApartments === 1 ? "იგივე ბინა" : `ამ პოსტთან ერთად ${uniqueApartments} სხვადასხვა ბინა`;
      reasons.push(`${where} ამ ნომრით/ავტორით კიდევ <b>${allRows.length - 1} განცხადება</b> (${flats})`);
    } else if (uniqueApartments >= 2 || allRows.length >= 2) {
      reasons.push(`${where} ამ ნომრით/ავტორით იძებნება <b>${allRows.length} განცხადება</b> (${uniqueApartments} სხვადასხვა ბინა)`);
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

function cleanFloorStr(f) {
  return String(f || "").trim().split("/")[0].trim();
}

function workerFloorsMatch(f1, f2) {
  const a = cleanFloorStr(f1);
  const b = cleanFloorStr(f2);
  if (!a || !b) return false;
  return a === b;
}

// Field comparisons return "agree", "conflict", or "unknown" (a side is missing).
// Unknown is never evidence for a match.
function compareLayout(target, cand) {
  const tr = parseInt(target?.rooms, 10) || 0;
  const cr = parseInt(cand?.rooms, 10) || 0;
  const tb = parseInt(target?.bedrooms, 10) || 0;
  const cb = parseInt(cand?.bedrooms, 10) || 0;
  if (tr > 0 && cr > 0) return tr === cr ? "agree" : "conflict";
  if (tb > 0 && cb > 0) return tb === cb ? "agree" : "conflict";
  // Golden FB posts that state both: rooms = bedrooms + 1 (22 of 23) or + 2 (1 of 23).
  const r = tr || cr;
  const b = tr ? cb : tb;
  if (r > 0 && b > 0) return r - b === 1 || r - b === 2 ? "agree" : "conflict";
  return "unknown";
}

function totalFloorsOf(listing) {
  const t = parseInt(listing?.total_floors, 10) || 0;
  if (t > 0) return t;
  const m = String(listing?.floor || "").match(/\/\s*(\d{1,2})/);
  return m ? parseInt(m[1], 10) : 0;
}

function compareTotalFloors(target, cand) {
  const a = totalFloorsOf(target);
  const b = totalFloorsOf(cand);
  if (!a || !b) return "unknown";
  if (a === b) return "agree";
  // Portals and posts may count a ground or attic floor differently.
  return Math.abs(a - b) === 1 ? "unknown" : "conflict";
}

// A row filed under the parent district is not evidence of the child district;
// only the child named in its subdistrict or street is. Two different micro-district
// numbers are two different flats.
function compareLocation(target, cand) {
  const tm = microOf(target);
  const cm = microOf(cand);
  if (tm && cm && tm !== cm) return "conflict";
  const d = String(target?.district || "").trim();
  if (!d) return "unknown";
  const stems = XRAY_DISTRICT_KEYWORDS.filter(([, canonical]) => canonical === d).map(([stem]) => stem);
  stems.push(d);
  const hay = `${cand?.subdistrict || ""} ${cand?.street || ""}`.toLowerCase();
  if (stems.some(s => hay.includes(s))) return "agree";
  if (tm && cm && expandDistrictAliases(d).includes(String(cand?.district || "").trim())) return "agree";
  return "unknown";
}

function workerCandidateMatchesTarget(target, cand) {
  const layout = compareLayout(target, cand);
  const height = compareTotalFloors(target, cand);
  const location = compareLocation(target, cand);
  if (layout === "conflict" || height === "conflict" || location === "conflict") return false;
  const tf = cleanFloorStr(target?.floor);
  const cf = cleanFloorStr(cand?.floor);
  if (tf) {
    if (cf !== tf) return false;
    // Floor and area alone fit many unrelated flats: require one more agreeing fact.
    return [layout, height, location].includes("agree");
  }
  const tr = parseInt(target?.rooms, 10) || 0;
  const cr = parseInt(cand?.rooms, 10) || 0;
  const tp = Number(target?.price_usd) || 0;
  const cp = Number(cand?.price_usd) || 0;
  if (tr > 0 && cr > 0 && tr === cr && tp > 0 && cp > 0) {
    return Math.abs(cp - tp) / tp <= 0.10;
  }
  return false;
}

async function queryWorkerExactDuplicates(db, target, limit = 25) {
  const dupMap = new Map();
  if (!db || !target) return [];
  const targetId = target.id || "";
  if (target.group_id) {
    const { results } = await db
      .prepare("SELECT * FROM properties WHERE group_id = ? AND id != ?")
      .bind(target.group_id, targetId)
      .all();
    for (const r of results || []) {
      if (r && r.id) dupMap.set(r.id, r);
    }
  }
  if (target.district && Number(target.area_m2) > 0) {
    const aliases = expandDistrictAliases(target.district);
    const placeholders = aliases.map(() => "?").join(", ");
    const area = Number(target.area_m2);
    const dealType = target.deal_type || "sale";
    const tf = cleanFloorStr(target.floor);
    const tr = parseInt(target.rooms, 10) || 0;
    const tp = Number(target.price_usd) || 0;

    const sqlParts = [
      `SELECT * FROM properties WHERE district IN (${placeholders})`,
      "AND area_m2 BETWEEN ? AND ?",
      "AND id != ?",
      "AND (deal_type IS NULL OR deal_type = ?)"
    ];
    const params = [...aliases, area - 1.5, area + 1.5, targetId, dealType];

    if (dealType === "sale") {
      sqlParts.push("AND price_usd >= 10000");
    }
    if (tr > 0) {
      sqlParts.push("AND (rooms IS NULL OR rooms <= 0 OR rooms = ?)");
      params.push(tr);
    }
    if (tf) {
      sqlParts.push("AND (floor = ? OR floor LIKE ?)");
      params.push(tf, `${tf}/%`);
      sqlParts.push(`ORDER BY price_usd ASC LIMIT ${limit}`);
    } else if (tp > 0) {
      sqlParts.push(`ORDER BY ABS(price_usd - ?) ASC LIMIT ${limit}`);
      params.push(tp);
    } else {
      sqlParts.push(`ORDER BY price_usd ASC LIMIT ${limit}`);
    }

    const { results } = await db.prepare(sqlParts.join(" ")).bind(...params).all();
    for (const r of results || []) {
      if (!r || !r.id) continue;
      if (!workerCandidateMatchesTarget(target, r)) continue;
      dupMap.set(r.id, r);
    }
  }
  return Array.from(dupMap.values()).sort((a, b) => (Number(a.price_usd) || 0) - (Number(b.price_usd) || 0));
}

async function enrichDuplicatesIfNeeded(db, dups, maxCount = 3) {
  const slice = (dups || []).slice(0, maxCount);
  await Promise.all(
    slice.map(async (dup) => {
      if (
        !dup ||
        (dup.phone_number &&
          !isMaskedPhone(dup.phone_number) &&
          dup.is_owner !== null &&
          dup.is_owner !== undefined &&
          dup.author_id &&
          dup.source !== "ss_ge")
      ) {
        return;
      }
      const srcId = dup.source_id || String(dup.id || "").replace(/^(?:myhome|ss_ge)_/, "");
      if (!srcId) return;
      const live = await fetchLiveStatementDetails(dup.source, srcId, dup.url);
      if (live) {
        const merged = mergeLiveIntoRow(dup, live);
        Object.assign(dup, merged);
        if (dup.id) {
          await persistLiveEnrichment(db, dup.id, merged);
        }
      }
    })
  );
}

// The phone report. `live` is myHomeLiveState(): { mode: "fresh" | "queued" | "failed" | "none", rows, full }.
async function buildPhoneReport(db, parsed, live) {
  const fmtParam = parsed.formattedPhone || parsed.maskedPhone || "";
  const p9Param = parsed.phone9 || fmtParam;
  const maskParam = parsed.maskedPhone || fmtParam;
  const { results } = await db
    .prepare("SELECT * FROM properties WHERE phone_number IN (?, ?, ?) ORDER BY scraped_at DESC LIMIT 30")
    .bind(fmtParam, p9Param, maskParam)
    .all();
  const fetched = results || [];
  let rows = fetched;
  if (parsed.phone9) {
    const exactRows = fetched.filter(
      r => r.phone_number === parsed.formattedPhone || r.phone_number === parsed.phone9
    );
    if (exactRows.length > 0) rows = exactRows;
    // Masked-prefix rows may be other people; do not mix them into an exact MyHome answer.
    else if (live.mode === "fresh" && live.rows.length > 0) rows = [];
  }

  const liveRows = live.rows || [];
  const indexById = new Map(rows.map((r, i) => [r.id, i]));
  for (const lr of liveRows) {
    if (!lr || !lr.id) continue;
    if (indexById.has(lr.id)) {
      const i = indexById.get(lr.id);
      rows[i] = mergeLiveIntoRow(rows[i], lr);
    } else {
      indexById.set(lr.id, rows.length);
      rows.push(lr);
    }
  }

  const header = `🔎 <b>ტელეფონის რენტგენი:</b> <code>${parsed.formattedPhone}</code>`;
  const searching = "🔄 myhome-ზე ვეძებ, დაახლოებით 1 წუთში მოგწერ.";
  if (rows.length === 0) {
    if (live.mode === "fresh") {
      return `${header}\n\nℹ️ ამ ნომერზე განცხადება ვერ მოიძებნა ვერც myhome-ზე და ვერც ჩვენს ბაზაში.`;
    }
    const note = { queued: `\n${searching}`, failed: "\n⚠️ myhome-ის შედეგი ახლა ვერ მივიღეთ." }[live.mode] || "";
    return `${header}\n\nℹ️ ჩვენს ბაზაში ამ ნომერზე განცხადებები ჯერ არ ფიქსირდება.${note}`;
  }

  // A row from the lookup job already carries what MyHome's listing page said.
  const liveIds = new Set(liveRows.map(r => r && r.id));
  if (
    !liveIds.has(rows[0].id) &&
    rows.length <= 5 &&
    (rows[0].is_owner === null ||
      rows[0].is_owner === undefined ||
      isMaskedPhone(rows[0].phone_number) ||
      !rows[0].author_id ||
      rows[0].author_listings_count === null ||
      rows[0].author_listings_count === undefined ||
      rows[0].source === "ss_ge")
  ) {
    const srcId = rows[0].source_id || String(rows[0].id || "").replace(/^(?:myhome|ss_ge)_/, "");
    const details = await fetchLiveStatementDetails(rows[0].source, srcId, rows[0].url);
    if (details) {
      rows[0] = mergeLiveIntoRow(rows[0], details);
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

  const verdict = evaluateWorkerBrokerVerdict(rows[0], rows, liveRows.length ? "myhome-ზე და ბაზაში" : "ბაზაში");
  const more = live.mode === "fresh" && live.full ? "+" : "";
  const lines = [header, `👤 <b>სტატუსი:</b> ${verdict.badge}`, `📌 ${verdict.reason}`];
  if (live.mode === "fresh") {
    lines.push(`🌐 myhome-ზე ამ ნომერზე <b>${liveRows.length}${more}</b> აქტიური განცხადებაა`);
  } else if (live.mode === "queued") {
    lines.push(liveRows.length ? `${searching} ქვემოთ ბოლოს ნანახიც ჩანს.` : searching);
  } else if (live.mode === "failed") {
    lines.push(
      liveRows.length
        ? "⚠️ myhome-ის შედეგი ახლა ვერ მივიღეთ, ნაჩვენებია ბოლოს ნანახი."
        : "⚠️ myhome-ის შედეგი ახლა ვერ მივიღეთ, ნაჩვენებია მხოლოდ ჩვენი ბაზა."
    );
  }
  lines.push("", `📋 <b>ნაპოვნი განცხადებები (${rows.length}${more}):</b>`);
  for (const r of rows.slice(0, 5)) {
    const p = Math.round(Number(r.price_usd) || 0).toLocaleString();
    const area = Number(r.area_m2) || 0;
    const dist = r.district || "უცნობი უბანი";
    const src = r.source === "myhome" ? "MyHome" : "SS.ge";
    lines.push(`• <a href="${r.url || "#"}">${src}</a> — <b>$${p}</b> | ${area} მ² | ${dist}`);
  }
  if (live.mode === "fresh" && liveRows.length > 0) {
    lines.push(`🔗 <a href="${myHomeSearchUrl({ q: parsed.phone9 })}">ყველა განცხადება myhome-ზე</a>`);
  }

  // Surface same-apartment duplicates (e.g. real owner listing) for matched listings
  const relatedIds = new Set(rows.map(r => r.id));
  const phoneDupMap = new Map();
  for (const r of rows.slice(0, 5)) {
    const refPrice = Number(r.price_usd) || 0;
    const rDups = await queryWorkerExactDuplicates(db, r, 15);
    for (const d of rDups) {
      if (d && d.id && !relatedIds.has(d.id) && !phoneDupMap.has(d.id)) {
        phoneDupMap.set(d.id, { dup: d, refPrice });
      }
    }
  }
  if (phoneDupMap.size > 0) {
    const sortedPhoneDups = Array.from(phoneDupMap.values()).sort(
      (a, b) => (Number(a.dup.price_usd) || 0) - (Number(b.dup.price_usd) || 0)
    );
    await enrichDuplicatesIfNeeded(db, sortedPhoneDups.map(item => item.dup), 3);
    lines.push("");
    lines.push("🎯 <b>იგივე ბინა სხვაგან:</b>");
    for (const { dup, refPrice } of sortedPhoneDups.slice(0, 4)) {
      const dPrice = Math.round(Number(dup.price_usd) || 0);
      const dSrc = dup.source === "myhome" ? "MyHome.ge" : "SS.ge";
      const dOwner = formatOwnerBadge(dup.is_owner);
      const dPhonePart = dup.phone_number ? ` | 📞 <code>${dup.phone_number}</code>` : "";
      let diffStr = "";
      if (refPrice > 0 && dPrice > 0) {
        const delta = Math.round(dPrice - refPrice);
        if (delta < 0) diffStr = ` (<b>-$${Math.abs(delta).toLocaleString()} უფრო იაფად!</b>)`;
        else if (delta > 0) diffStr = ` (+$${delta.toLocaleString()} უფრო ძვირად)`;
        else diffStr = " (იგივე ფასი)";
      }
      lines.push(`• <a href="${dup.url || "#"}">${dSrc}</a> — <b>$${dPrice.toLocaleString()}</b>${diffStr} | ${dOwner}${dPhonePart}`);
    }
  }

  return lines.join("\n");
}

async function handleXRayQuery(text, env, opts = {}) {
  const parsed = parseXRayInput(text);
  if (!parsed) return null;

  const db = env?.DB;
  if (!db) {
    return "⚠️ D1 ბაზა არ არის დაკავშირებული Cloudflare Worker-თან.";
  }

  try {
    if (parsed.kind === "phone") {
      if (opts.collect && parsed.phone9) {
        opts.collect.ownerTarget = ownerTarget({ ...(parsed.flat || {}), phone9: parsed.phone9 }, text);
      }
      // MyHome is read by a GitHub Actions job (ADR 0004); D1 holds only what matched subscriber filters.
      return await buildPhoneReport(db, parsed, await myHomeLiveState(env, parsed, opts.chatId));
    }

    let target = null;
    // "queued" or "failed" when a MyHome listing page has to be read by the lookup job (ADR 0004).
    let myhomeCheck = null;
    if (parsed.kind === "url") {
      const row = await db
        .prepare("SELECT * FROM properties WHERE id = ?")
        .bind(parsed.listingId)
        .first();
      if (row) {
        target = row;
      }
      const needsLive =
        !target ||
        target.is_owner === null ||
        target.is_owner === undefined ||
        !target.phone_number ||
        !target.author_id ||
        target.author_listings_count === null ||
        target.author_listings_count === undefined ||
        target.source === "ss_ge";
      if (parsed.source === "myhome") {
        // MyHome answers the Worker with a challenge page, so the lookup job reads the listing.
        const saved = await loadSavedListing(db, parsed.listingId);
        if (saved && isFreshIso(saved.seen_at, MYHOME_LOOKUP_FRESH_MINUTES)) {
          target = target ? mergeLiveIntoRow(target, saved) : saved;
        } else if (needsLive) {
          if (!target && saved) target = saved;
          const jobId = await queueMyHomeLookup(env, opts.chatId, { kind: "listing", listingId: parsed.sourceId });
          myhomeCheck = jobId ? "queued" : "failed";
        }
      } else if (needsLive) {
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
        bedrooms: parsed.bedrooms || null,
        total_floors: parsed.total_floors || null,
        phone_number: parsed.formattedPhone,
        is_owner: null,
        user_type: null
      };
    }

    if (!target) {
      if (myhomeCheck === "queued") {
        return (
          "🔎 <b>განცხადების რენტგენი</b>\n\n" +
          "ℹ️ ეს განცხადება ჩვენს ბაზაში ჯერ არ არის.\n" +
          "🔄 myhome-ზე ვამოწმებ, დაახლოებით 1 წუთში მოგწერ."
        );
      }
      return (
        "🔎 <b>განცხადების რენტგენი</b>\n\n" +
        "ℹ️ ეს განცხადება ჩვენს ბაზაში ჯერ არ იძებნება და პორტალიდან მონაცემების წამოღება ვერ მოხერხდა."
      );
    }

    if (opts.collect) {
      opts.collect.ownerTarget = ownerTarget({
        ...target,
        micro: parsed.micro,
        listing_id: parsed.kind === "url" ? target.id : null,
        phone9: parsed.phone9 || extract9DigitPhone(target.phone_number || "")
      }, text);
    }

    // Find exact duplicates and live-enrich top matches so missing phones/owner badges are recovered
    const duplicates = await queryWorkerExactDuplicates(db, target, 25);
    if (duplicates.length > 0) {
      await enrichDuplicatesIfNeeded(db, duplicates, 3);
    }

    let phone9 = parsed.phone9 || extract9DigitPhone(target.phone_number || "");
    let maskedPhone = parsed.maskedPhone || (phone9 ? `${phone9.slice(0, 6)}***` : extractMaskedPhone(target.phone_number || ""));
    let verdictTarget = target;
    if (parsed.kind === "fb_text" && !phone9 && !maskedPhone && duplicates.length > 0) {
      verdictTarget = duplicates[0];
      phone9 = extract9DigitPhone(verdictTarget.phone_number || "");
      maskedPhone = phone9 ? `${phone9.slice(0, 6)}***` : extractMaskedPhone(verdictTarget.phone_number || "");
    }

    // A post with its own full number is looked up on MyHome like a bare number (ADR 0004),
    // so the verdict does not rest on rows that only carry the masked number.
    let live = null;
    if (parsed.kind === "fb_text" && parsed.phone9 && opts.chatId) {
      live = await myHomeLiveState(env, parsed, opts.chatId);
    }

    const relatedMap = new Map();
    if (phone9 || maskedPhone) {
      const fmt = phone9 ? formatGeorgianPhone(phone9) : maskedPhone;
      const p9Param = phone9 || fmt;
      const maskParam = maskedPhone || fmt;
      const { results } = await db
        .prepare("SELECT * FROM properties WHERE phone_number IN (?, ?, ?) ORDER BY scraped_at DESC LIMIT 30")
        .bind(fmt, p9Param, maskParam)
        .all();
      const fetched = results || [];
      let chosen = fetched;
      if (phone9) {
        const exactRows = fetched.filter(r => r.phone_number === fmt || r.phone_number === phone9);
        if (exactRows.length > 0) chosen = exactRows;
        // Masked-prefix rows may be other people; do not mix them into an exact MyHome answer.
        else if (live && live.mode === "fresh" && live.rows.length > 0) chosen = [];
      }
      for (const r of chosen) relatedMap.set(r.id, r);
    }
    for (const lr of (live && live.rows) || []) {
      if (lr && lr.id) relatedMap.set(lr.id, relatedMap.has(lr.id) ? mergeLiveIntoRow(relatedMap.get(lr.id), lr) : lr);
    }
    if (verdictTarget.author_id) {
      const { results } = await db
        .prepare("SELECT * FROM properties WHERE author_id = ? ORDER BY scraped_at DESC LIMIT 30")
        .bind(String(verdictTarget.author_id))
        .all();
      for (const r of results || []) relatedMap.set(r.id, r);
    }

    const verdict = evaluateWorkerBrokerVerdict(
      verdictTarget,
      Array.from(relatedMap.values()),
      live && live.rows.length ? "myhome-ზე და ბაზაში" : "ბაზაში"
    );

    // District benchmark: MyHome's own average $/m² for this district and bedroom count.
    // Nothing falls back to our own rows: they are only what this bot happened to scrape.
    let bench = null;
    if (target.district) {
      const table = await fetchMyHomeDistrictPrices(workerBedroomTypeFor(target.rooms, target.bedrooms));
      const price = lookupMyHomePrice(table, expandDistrictAliases(target.district));
      if (price) bench = { avg_m2: Math.round(price) };
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
    if (myhomeCheck === "queued") {
      lines.push("🔄 myhome-ზე ვამოწმებ განცხადებას, დაახლოებით 1 წუთში მოგწერ ზუსტ სტატუსს.");
    } else if (myhomeCheck === "failed") {
      lines.push("⚠️ myhome-ის შედეგი ახლა ვერ მივიღეთ, სტატუსი ჩვენი ბაზით არის.");
    }
    if (live && live.mode === "queued") {
      lines.push("🔄 myhome-ზე ამ ნომერს ვამოწმებ, დაახლოებით 1 წუთში მოგწერ.");
    } else if (live && live.mode === "failed") {
      lines.push("⚠️ myhome-ის შედეგი ახლა ვერ მივიღეთ, სტატუსი ჩვენი ბაზით არის.");
    }
    const displayPhone = target.phone_number || (duplicates.find(d => d && d.phone_number)?.phone_number) || null;
    if (displayPhone) {
      lines.push(`📞 <b>ტელეფონი:</b> <code>${displayPhone}</code>`);
    }

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
      for (const [i, dup] of duplicates.slice(0, 4).entries()) {
        const dupPrice = Math.round(Number(dup.price_usd) || 0);
        const dupSrc = dup.source === "myhome" ? "MyHome.ge" : "SS.ge";
        const dupOwner = formatOwnerBadge(dup.is_owner);
        const dupPhonePart = dup.phone_number ? ` | 📞 <code>${dup.phone_number}</code>` : "";
        let diffStr = "";
        if (currPrice > 0 && dupPrice > 0) {
          const delta = Math.round(dupPrice - currPrice);
          if (delta < 0) diffStr = ` (<b>-$${Math.abs(delta).toLocaleString()} უფრო იაფად!</b>)`;
          else if (delta > 0) diffStr = ` (+$${delta.toLocaleString()} უფრო ძვირად)`;
          else diffStr = " (იგივე ფასი)";
        }
        lines.push(`${i + 1}. <a href="${dup.url || "#"}">${dupSrc}</a> — <b>$${dupPrice.toLocaleString()}</b>${diffStr} | ${dupOwner}${dupPhonePart}`);
      }
    } else {
      lines.push("🎯 <b>იგივე ბინა სხვაგან:</b> დუბლიკატი სხვა პორტალზე არ იძებნება.");
    }

    // What the ✅/❌ buttons under the reply refer to (see saveXRayResult).
    if (opts.collect && duplicates.length > 0) {
      opts.collect.duplicates = duplicates.slice(0, 4).map(d => ({ id: d.id, url: d.url || null }));
      opts.collect.resultTarget = xrayResultTarget(target);
    }

    return lines.join("\n");
  } catch (err) {
    console.error("X-Ray query error:", err);
    return null;
  }
}
