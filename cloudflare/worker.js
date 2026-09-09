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
    if (targetUrl && request.method === "GET") {
      return handleProxy(targetUrl, request);
    }

    // 2. Protected Sync API for Python Runner: GET /api/users
    if (url.pathname === "/api/users") {
      return handleApiUsers(request, env);
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
    headers.set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36");
    headers.set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8");
    headers.set("Accept-Language", "ka-GE,ka;q=0.9,en-US;q=0.8,en;q=0.7");

    const response = await fetch(parsed.toString(), {
      method: "GET",
      headers: headers
    });

    return new Response(response.body, {
      status: response.status,
      headers: {
        "content-type": response.headers.get("content-type") || "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*"
      }
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
            rent_price_min_usd: user.rent_price_min_usd !== undefined ? user.rent_price_min_usd : 300,
            rent_price_max_usd: user.rent_price_max_usd !== undefined ? user.rent_price_max_usd : 1500,
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
      await handleMessage(update.message, token, kv);
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
async function handleMessage(msg, token, kv) {
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
      `ეს ბოტი ავტომატურად ასკანერებს <b>MyHome.ge</b> და <b>SS.ge</b> პორტალებს 24/7 რეჟიმში და ახალ ბინებს მომენტალურად გიგზავნით.\n\n` +
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

  // Fallback for unrecognized text
  await sendTelegram(token, "sendMessage", {
    chat_id: chatId,
    text: `გთხოვთ გამოიყენოთ მენიუ ან ბრძანება /filter პარამეტრების სამართავად.`,
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
