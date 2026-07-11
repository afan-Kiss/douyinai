/**
 * Browser integration tests via jsdom + mocked fetch.
 * Run: node tests/test_ui_integration.mjs
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const uiDir = path.join(root, "desktop/ui");

let passed = 0;
let failed = 0;
let unhandledRejections = 0;

process.on("unhandledRejection", () => {
  unhandledRejections += 1;
});

function delay(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function waitUntil(fn, { timeout = 4000, interval = 25 } = {}) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (fn()) return;
    await delay(interval);
  }
  throw new Error("waitUntil timeout");
}

function test(name, fn) {
  return Promise.resolve()
    .then(async () => {
      unhandledRejections = 0;
      await fn();
      assert.equal(unhandledRejections, 0, "unhandled promise rejection");
      passed += 1;
      console.log(`OK ${name}`);
    })
    .catch((e) => {
      failed += 1;
      console.error(`FAIL ${name}:`, e.message);
    });
}

function minimalHtml() {
  const index = fs.readFileSync(path.join(uiDir, "index.html"), "utf8");
  return index.replace(/<script[\s\S]*<\/script>/gi, "");
}

function jsonResponse(body, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    headers: { get: (name) => (String(name).toLowerCase() === "content-type" ? "application/json" : "") },
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

function createApp(fetchImpl) {
  const dom = new JSDOM(minimalHtml(), { url: "http://127.0.0.1:8765/", runScripts: "outside-only" });
  const { window } = dom;
  window.fetch = (url, opt) => fetchImpl(url, opt);
  window.confirm = () => true;
  window.alert = () => {};
  window.prompt = () => "";
  window.__PIGEON_TEST_MODE__ = true;
  const context = dom.getInternalVMContext();
  vm.runInContext(fs.readFileSync(path.join(uiDir, "app-state.js"), "utf8"), context);
  vm.runInContext(fs.readFileSync(path.join(uiDir, "app.js"), "utf8"), context);
  const app = window.__PIGEON_TEST__;
  app.bindEvents();
  return { window, app, dom };
}

function baseSessionState(app) {
  app.state.loggedIn = true;
  app.state.activeAccountId = "shop_a";
  app.state.accountGeneration = 1;
  app.state.aiMode = "confirm";
}

await test("btnStartQr click calls /api/qr-login/start once", async () => {
  const calls = [];
  const { window, app } = createApp(async (url) => {
    calls.push(String(url));
    if (String(url).includes("/api/qr-login/start")) {
      return jsonResponse({ ok: true, qr: { phase: "waiting_scan", running: true, job_id: "j1" } });
    }
    if (String(url).includes("/api/health")) {
      return jsonResponse({ ok: true, bridge_ready: true });
    }
    return jsonResponse({ ok: true });
  });
  window.document.getElementById("btnStartQr").click();
  await waitUntil(() => calls.filter((u) => u.includes("/api/qr-login/start")).length === 1);
  assert.equal(calls.filter((u) => u.includes("/api/qr-login/start")).length, 1);
  assert.equal(app.state.qrPollingActive, true);
});

await test("double click btnStartQr while lock busy only starts once", async () => {
  let startCount = 0;
  const { window } = createApp(async (url) => {
    if (String(url).includes("/api/qr-login/start")) {
      startCount += 1;
      await delay(40);
      return jsonResponse({ ok: true, qr: { phase: "waiting_scan", running: true, job_id: "j1" } });
    }
    if (String(url).includes("/api/health")) {
      return jsonResponse({ ok: true, bridge_ready: true });
    }
    return jsonResponse({ ok: true });
  });
  const btn = window.document.getElementById("btnStartQr");
  btn.click();
  btn.click();
  await delay(120);
  assert.equal(startCount, 1);
});

await test("switchAccount increments accountGeneration", async () => {
  const { app } = createApp(async (url) => {
    if (String(url).includes("/api/accounts/switch")) {
      return jsonResponse({ ok: true, active_account_id: "shop_b", account_id: "shop_b" });
    }
    if (String(url).includes("/api/session")) {
      return jsonResponse({
        logged_in: true,
        active_account_id: "shop_b",
        accounts: [{ id: "shop_b", logged_in: true }],
      });
    }
    if (String(url).includes("/api/conversations")) {
      return jsonResponse({ ok: true, items: [] });
    }
    if (String(url).includes("/api/listen")) {
      return jsonResponse({ ok: true, running: false });
    }
    return jsonResponse({ ok: true });
  });
  baseSessionState(app);
  app.state.accounts = [{ id: "shop_a", logged_in: true }, { id: "shop_b", logged_in: true }];
  const gen0 = app.state.accountGeneration;
  await app.switchAccount("shop_b");
  assert.ok(app.state.accountGeneration > gen0);
  assert.equal(app.state.activeAccountId, "shop_b");
});

await test("switch failure restores buyer title and composer", async () => {
  const { window, app } = createApp(async (url) => {
    if (String(url).includes("/api/accounts/switch")) {
      return jsonResponse({ ok: false, error: "denied" });
    }
    return jsonResponse({ ok: true });
  });
  baseSessionState(app);
  app.state.currentUid = "u1";
  app.state.composerDraftsByKey["shop_a:u1"] = { text: "draft-a", fromAi: false, updatedAt: Date.now() };
  window.document.getElementById("buyerTitle").textContent = "买家A";
  window.document.getElementById("composerInput").value = "draft-a";
  await app.switchAccount("shop_b");
  assert.equal(window.document.getElementById("buyerTitle").textContent, "买家A");
  assert.equal(window.document.getElementById("composerInput").value, "draft-a");
  assert.equal(app.state.activeAccountId, "shop_a");
});

await test("selectConversation loads orders into DOM without ReferenceError", async () => {
  const { window, app } = createApp(async (url) => {
    if (String(url).includes("/api/context")) {
      return jsonResponse({ ok: true, messages: [{ role: "buyer", text: "hi" }] });
    }
    if (String(url).includes("/api/orders")) {
      return jsonResponse({
        ok: true,
        orders: {
          has_order: true,
          cards: [{ product_name: "测试商品A", amount: "99", status: "待发货" }],
        },
      });
    }
    if (String(url).includes("/api/conversations/ack")) {
      return jsonResponse({ ok: true });
    }
    return jsonResponse({ ok: true });
  });
  baseSessionState(app);
  await app.selectConversation("buyer_a");
  await waitUntil(() => !app.state.ordersLoading);
  const html = window.document.getElementById("orderBody").innerHTML;
  assert.ok(!html.includes("order-loading"), "order skeleton should disappear");
  assert.ok(html.includes("测试商品A"), "order content should render");
  assert.equal(app.state.ordersLoading, false);
  assert.equal(app.state.contextLoading, false);
});

await test("stale orders response after buyer switch does not land", async () => {
  let resolveOrdersA;
  const ordersAPromise = new Promise((r) => {
    resolveOrdersA = r;
  });
  const { window, app } = createApp(async (url) => {
    if (String(url).includes("/api/context")) {
      return jsonResponse({ ok: true, messages: [] });
    }
    if (String(url).includes("/api/orders") && url.includes("buyer_a")) {
      await ordersAPromise;
      return jsonResponse({
        ok: true,
        orders: { has_order: true, cards: [{ product_name: "A专属订单", amount: "1", status: "x" }] },
      });
    }
    if (String(url).includes("/api/orders")) {
      return jsonResponse({
        ok: true,
        orders: { has_order: true, cards: [{ product_name: "B专属订单", amount: "2", status: "y" }] },
      });
    }
    if (String(url).includes("/api/conversations/ack")) {
      return jsonResponse({ ok: true });
    }
    return jsonResponse({ ok: true });
  });
  baseSessionState(app);
  const pA = app.selectConversation("buyer_a");
  await delay(30);
  await app.selectConversation("buyer_b");
  resolveOrdersA();
  await pA;
  await waitUntil(() => !app.state.ordersLoading);
  const html = window.document.getElementById("orderBody").innerHTML;
  assert.ok(html.includes("B专属订单"));
  assert.ok(!html.includes("A专属订单"));
});

await test("orders timeout shows retry state not permanent loading", async () => {
  const { window, app } = createApp(async (url) => {
    if (String(url).includes("/api/context")) {
      return jsonResponse({ ok: true, messages: [] });
    }
    if (String(url).includes("/api/orders")) {
      return jsonResponse({ ok: false, timeout: true, error: "请求超时" });
    }
    if (String(url).includes("/api/conversations/ack")) {
      return jsonResponse({ ok: true });
    }
    return jsonResponse({ ok: true });
  });
  baseSessionState(app);
  await app.selectConversation("buyer_a");
  await waitUntil(() => !app.state.ordersLoading);
  assert.equal(app.state.ordersLoading, false);
  assert.match(app.state.ordersError, /超时|重试/);
  const html = window.document.getElementById("orderBody").innerHTML;
  assert.ok(!html.includes("order-loading"));
});

await test("composer draft isolated per buyer", async () => {
  const { window, app } = createApp(async (url) => {
    if (String(url).includes("/api/context") || String(url).includes("/api/orders")) {
      return jsonResponse({ ok: true, messages: [], orders: { has_order: false, cards: [] } });
    }
    if (String(url).includes("/api/conversations/ack")) {
      return jsonResponse({ ok: true });
    }
    return jsonResponse({ ok: true });
  });
  baseSessionState(app);
  await app.selectConversation("buyer_a");
  window.document.getElementById("composerInput").value = "给 A 的回复";
  app.saveComposerDraftForCurrent();
  await app.selectConversation("buyer_b");
  assert.equal(window.document.getElementById("composerInput").value, "");
});

await test("composer draft restores when switching back", async () => {
  const { window, app } = createApp(async (url) => {
    if (String(url).includes("/api/context") || String(url).includes("/api/orders")) {
      return jsonResponse({ ok: true, messages: [], orders: { has_order: false, cards: [] } });
    }
    if (String(url).includes("/api/conversations/ack")) {
      return jsonResponse({ ok: true });
    }
    return jsonResponse({ ok: true });
  });
  baseSessionState(app);
  await app.selectConversation("buyer_a");
  window.document.getElementById("composerInput").value = "给 A 的回复";
  await app.selectConversation("buyer_b");
  window.document.getElementById("composerInput").value = "给 B 的回复";
  await app.selectConversation("buyer_a");
  assert.equal(window.document.getElementById("composerInput").value, "给 A 的回复");
});

await test("AI draft does not appear in another buyer composer", async () => {
  const { window, app } = createApp(async (url) => {
    if (String(url).includes("/api/ai/suggest")) {
      return jsonResponse({ ok: true, reply: "AI给A", intent: "other" });
    }
    if (String(url).includes("/api/context") || String(url).includes("/api/orders")) {
      return jsonResponse({ ok: true, messages: [], orders: { has_order: false, cards: [] } });
    }
    if (String(url).includes("/api/conversations/ack")) {
      return jsonResponse({ ok: true });
    }
    return jsonResponse({ ok: true });
  });
  baseSessionState(app);
  app.state.currentUid = "buyer_a";
  app.state.messages = [{ role: "buyer", text: "q" }];
  await app.generateAiReply("q");
  await app.selectConversation("buyer_b");
  assert.notEqual(window.document.getElementById("composerInput").value, "AI给A");
});

await test("same uid different accountId keeps composer drafts isolated", async () => {
  const { window, app } = createApp(async () => jsonResponse({ ok: true }));
  app.state.activeAccountId = "shop_a";
  app.state.currentUid = "uid_shared";
  window.document.getElementById("composerInput").value = "账号A草稿";
  app.saveComposerDraftForCurrent();
  app.state.activeAccountId = "shop_b";
  app.loadComposerDraftForUid("uid_shared");
  assert.equal(window.document.getElementById("composerInput").value, "");
  app.state.composerDraftsByKey["shop_b:uid_shared"] = { text: "账号B草稿", fromAi: false, updatedAt: Date.now() };
  app.loadComposerDraftForUid("uid_shared");
  assert.equal(window.document.getElementById("composerInput").value, "账号B草稿");
});

await test("manual send with ai paused clears composer on success", async () => {
  const { window, app } = createApp(async (url) => {
    if (String(url).includes("/api/send")) {
      return jsonResponse({ ok: true });
    }
    if (String(url).includes("/api/context") || String(url).includes("/api/orders")) {
      return jsonResponse({ ok: true, messages: [], orders: { has_order: false, cards: [] } });
    }
    if (String(url).includes("/api/conversations/ack")) {
      return jsonResponse({ ok: true });
    }
    return jsonResponse({ ok: true });
  });
  baseSessionState(app);
  app.state.aiMode = "pause";
  app.state.currentUid = "buyer_a";
  window.document.getElementById("composerInput").value = "手工发送";
  await app.sendMessage();
  assert.equal(window.document.getElementById("composerInput").value, "");
});

await test("manual send with human takeover succeeds via send button", async () => {
  let sendCount = 0;
  const { window, app } = createApp(async (url) => {
    if (String(url).includes("/api/send")) {
      sendCount += 1;
      return jsonResponse({ ok: true });
    }
    if (String(url).includes("/api/context") || String(url).includes("/api/orders")) {
      return jsonResponse({ ok: true, messages: [], orders: { has_order: false, cards: [] } });
    }
    if (String(url).includes("/api/conversations/ack")) {
      return jsonResponse({ ok: true });
    }
    return jsonResponse({ ok: true });
  });
  baseSessionState(app);
  app.state.humanTakeover = true;
  app.state.currentUid = "buyer_a";
  window.document.getElementById("composerInput").value = "人工回复";
  window.document.getElementById("btnSend").click();
  await waitUntil(() => sendCount === 1);
  await waitUntil(() => window.document.getElementById("composerInput").value === "");
  assert.equal(sendCount, 1);
});

await test("manual send during buyer switch does not clear new buyer composer", async () => {
  let resolveSend;
  const sendPromise = new Promise((r) => {
    resolveSend = r;
  });
  const { window, app } = createApp(async (url) => {
    if (String(url).includes("/api/send")) {
      await sendPromise;
      return jsonResponse({ ok: true });
    }
    if (String(url).includes("/api/context") || String(url).includes("/api/orders")) {
      return jsonResponse({ ok: true, messages: [], orders: { has_order: false, cards: [] } });
    }
    if (String(url).includes("/api/conversations/ack")) {
      return jsonResponse({ ok: true });
    }
    return jsonResponse({ ok: true });
  });
  baseSessionState(app);
  app.state.currentUid = "buyer_a";
  window.document.getElementById("composerInput").value = "A消息";
  const sendP = app.sendMessage();
  await app.selectConversation("buyer_b");
  window.document.getElementById("composerInput").value = "B保留";
  resolveSend();
  await sendP;
  assert.equal(window.document.getElementById("composerInput").value, "B保留");
});

await test("double click send only triggers one request", async () => {
  let sendCount = 0;
  const { window, app } = createApp(async (url) => {
    if (String(url).includes("/api/send")) {
      sendCount += 1;
      await delay(50);
      return jsonResponse({ ok: true });
    }
    if (String(url).includes("/api/context") || String(url).includes("/api/orders")) {
      return jsonResponse({ ok: true, messages: [], orders: { has_order: false, cards: [] } });
    }
    if (String(url).includes("/api/conversations/ack")) {
      return jsonResponse({ ok: true });
    }
    return jsonResponse({ ok: true });
  });
  baseSessionState(app);
  app.state.currentUid = "buyer_a";
  window.document.getElementById("composerInput").value = "一次发送";
  window.document.getElementById("btnSend").click();
  window.document.getElementById("btnSend").click();
  await delay(200);
  assert.equal(sendCount, 1);
});

await test("pollEvents stale response does not mutate convMeta or DOM", async () => {
  let resolveEvents;
  const eventsPromise = new Promise((r) => {
    resolveEvents = r;
  });
  const { app } = createApp(async (url) => {
    if (String(url).includes("/api/events")) {
      await eventsPromise;
      return jsonResponse({
        ok: true,
        items: [
          {
            seq: 9,
            kind: "message",
            message: { security_user_id: "u_stale", text: "stale-msg", role: "buyer" },
          },
        ],
      });
    }
    return jsonResponse({ ok: true });
  });
  app.state.listenOn = true;
  app.state.activeAccountId = "shop_a";
  app.state.accountGeneration = 2;
  app.state.listenGeneration = 2;
  app.state.conversations = [];
  const pollP = app.pollEvents();
  app.state.accountGeneration = 3;
  app.state.activeAccountId = "shop_b";
  app.state.listenGeneration = 3;
  resolveEvents();
  await pollP;
  assert.equal(app.state.convMeta.u_stale?.lastPreview, undefined);
});

await test("refreshLogin catch after account switch does not restore old account", async () => {
  const { app } = createApp(async (url) => {
    if (String(url).includes("/api/session")) {
      throw new Error("network down");
    }
    return jsonResponse({ ok: true });
  });
  app.commitTrustedAuthSnapshot({
    loggedIn: true,
    activeAccountId: "shop_a",
    accounts: [{ id: "shop_a", logged_in: true }],
  });
  app.state.activeAccountId = "shop_b";
  app.state.authGeneration = 3;
  const restored = app.restoreTrustedAuth({
    currentAuthGeneration: app.state.authGeneration,
    requiredAccountId: "shop_b",
  });
  assert.equal(restored, false);
  assert.equal(app.state.activeAccountId, "shop_b");
});

await test("refreshLogin catch can restore when generation and account match", async () => {
  const { app } = createApp(async () => {
    throw new Error("network down");
  });
  app.commitTrustedAuthSnapshot({
    loggedIn: true,
    activeAccountId: "shop_a",
    accounts: [{ id: "shop_a", logged_in: true }],
  });
  app.state.authGeneration = app.state.authLastTrusted.generation;
  app.state.activeAccountId = "shop_a";
  const restored = app.restoreTrustedAuth({
    currentAuthGeneration: app.state.authGeneration,
    requiredAccountId: "shop_a",
  });
  assert.equal(restored, true);
  assert.equal(app.state.loggedIn, true);
});

await test("qr switched_from bumps accountGeneration and stale conv refresh ignored", async () => {
  let resolveConv;
  const convPromise = new Promise((r) => {
    resolveConv = r;
  });
  const { app } = createApp(async (url) => {
    if (String(url).includes("/api/qr-login/start")) {
      return jsonResponse({
        ok: true,
        switched_from: "shop_a",
        account_id: "shop_b",
        qr: { phase: "waiting_scan", running: true, job_id: "j2" },
      });
    }
    if (String(url).includes("/api/health")) {
      return jsonResponse({ ok: true, bridge_ready: true });
    }
    if (String(url).includes("/api/conversations")) {
      await convPromise;
      return jsonResponse({
        ok: true,
        items: [{ security_user_id: "u_old", display_name: "旧会话" }],
      });
    }
    return jsonResponse({ ok: true });
  });
  app.state.loggedIn = true;
  app.state.activeAccountId = "shop_a";
  app.state.conversations = [{ security_user_id: "u_keep", display_name: "保留" }];
  const accountGenBeforeConv = app.state.accountGeneration;
  const convP = app.refreshConversations(true, "all", { heavy: false });
  await delay(20);
  const gen0 = app.state.accountGeneration;
  await app.startQrLogin();
  assert.ok(app.state.accountGeneration > gen0);
  assert.equal(app.state.activeAccountId, "shop_b");
  assert.equal(app.state.currentUid, "");
  resolveConv();
  await convP;
  assert.equal(app.state.activeAccountId, "shop_b");
  assert.ok(app.state.accountGeneration > accountGenBeforeConv);
  assert.ok(!app.state.conversations.some((c) => c.security_user_id === "u_old"));
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
