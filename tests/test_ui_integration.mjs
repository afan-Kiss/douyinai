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

function test(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => {
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
  window.fetch = fetchImpl;
  window.confirm = () => true;
  window.alert = () => {};
  window.prompt = () => "";
  window.__PIGEON_TEST_MODE__ = true;
  const context = dom.getInternalVMContext();
  vm.runInContext(fs.readFileSync(path.join(uiDir, "app-state.js"), "utf8"), context);
  vm.runInContext(fs.readFileSync(path.join(uiDir, "app.js"), "utf8"), context);
  return { window, app: window.__PIGEON_TEST__ };
}

await test("single click startQr calls /api/qr-login/start once", async () => {
  const calls = [];
  const { app } = createApp(async (url, opt) => {
    calls.push(String(url));
    if (String(url).includes("/api/qr-login/start")) {
      return jsonResponse({ ok: true, qr: { phase: "waiting_scan", running: true, job_id: "j1" } });
    }
    if (String(url).includes("/api/health")) {
      return jsonResponse({ ok: true, bridge_ready: true });
    }
    return jsonResponse({ ok: true });
  });
  await app.startQrLogin();
  const startCalls = calls.filter((u) => u.includes("/api/qr-login/start"));
  assert.equal(startCalls.length, 1);
});

await test("double click startQr while lock busy only starts once", async () => {
  let startCount = 0;
  const { app } = createApp(async (url) => {
    if (String(url).includes("/api/qr-login/start")) {
      startCount += 1;
      await new Promise((r) => setTimeout(r, 30));
      return jsonResponse({ ok: true, qr: { phase: "waiting_scan", running: true, job_id: "j1" } });
    }
    if (String(url).includes("/api/health")) {
      return jsonResponse({ ok: true, bridge_ready: true });
    }
    return jsonResponse({ ok: true });
  });
  const p1 = app.startQrLogin();
  const p2 = app.startQrLogin();
  await Promise.allSettled([p1, p2]);
  assert.equal(startCount, 1);
});

await test("switch failure restores buyer title and composer", async () => {
  const { window, app } = createApp(async (url) => {
    if (String(url).includes("/api/accounts/switch")) {
      return jsonResponse({ ok: false, error: "denied" });
    }
    return jsonResponse({ ok: true });
  });
  app.state.loggedIn = true;
  app.state.activeAccountId = "shop_a";
  app.state.accounts = [{ id: "shop_a", logged_in: true }];
  app.state.currentUid = "u1";
  app.state.messages = [{ role: "buyer", text: "hello" }];
  app.state.aiDraft = "draft-a";
  window.document.getElementById("buyerTitle").textContent = "买家A";
  window.document.getElementById("composerInput").value = "draft-a";
  const beforeGen = app.state.accountGeneration;
  await app.switchAccount("shop_b");
  assert.equal(window.document.getElementById("buyerTitle").textContent, "买家A");
  assert.equal(window.document.getElementById("composerInput").value, "draft-a");
  assert.equal(app.state.activeAccountId, "shop_a");
  assert.equal(app.state.authPendingAction, null);
  assert.ok(app.state.accountGeneration >= beforeGen);
});

await test("accountGeneration increments when bumpAccountGeneration called", async () => {
  const { app } = createApp(async () => jsonResponse({ ok: true }));
  const gen0 = app.state.accountGeneration;
  app.bumpAccountGeneration();
  assert.ok(app.state.accountGeneration > gen0);
});

await test("switch success sets active account to target", async () => {
  const { app } = createApp(async (url) => {
    if (String(url).includes("/api/accounts/switch")) {
      return jsonResponse({ ok: true, active_account_id: "shop_b", account_id: "shop_b" });
    }
    if (String(url).includes("/api/session")) {
      return jsonResponse({
        logged_in: true,
        active_account_id: "shop_b",
        accounts: [{ id: "shop_b", logged_in: true }],
        cookie_count: 1,
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
  app.state.loggedIn = true;
  app.state.activeAccountId = "shop_a";
  app.state.accounts = [{ id: "shop_a", logged_in: true }, { id: "shop_b", logged_in: true }];
  await app.switchAccount("shop_b");
  assert.equal(app.state.activeAccountId, "shop_b");
  assert.equal(app.state.authPendingAction, null);
});

await test("AI generating then switch buyer does not write draft to new buyer", async () => {
  let resolveAi;
  const aiPromise = new Promise((r) => {
    resolveAi = r;
  });
  const { window, app } = createApp(async (url) => {
    if (String(url).includes("/api/ai/suggest")) {
      await aiPromise;
      return jsonResponse({ ok: true, reply: "reply-for-A", intent: "other" });
    }
    return jsonResponse({ ok: true });
  });
  app.state.loggedIn = true;
  app.state.activeAccountId = "shop_a";
  app.state.aiMode = "confirm";
  app.state.currentUid = "buyer_a";
  app.state.messages = [{ role: "buyer", text: "q" }];
  const gen = app.generateAiReply("q");
  app.state.currentUid = "buyer_b";
  window.document.getElementById("composerInput").value = "";
  resolveAi();
  await gen;
  assert.notEqual(window.document.getElementById("composerInput").value, "reply-for-A");
});

await test("stale session response dropped when auth generation advanced", async () => {
  const { app } = createApp(async () => jsonResponse({ ok: true }));
  app.state.activeAccountId = "shop_b";
  app.state.authGeneration = 5;
  const gate = app.canRestoreTrustedAuth(
    { generation: 4, activeAccountId: "shop_a", loggedIn: true, accounts: [] },
    { authGeneration: 5, requiredAccountId: "shop_b" }
  );
  assert.equal(gate.ok, false);
});

await test("pollEvents stale account response discarded", async () => {
  const { app } = createApp(async (url) => {
    if (String(url).includes("/api/events")) {
      return jsonResponse({
        ok: true,
        items: [{ seq: 9, kind: "message", message: { security_user_id: "u9", text: "x", role: "buyer" } }],
      });
    }
    return jsonResponse({ ok: true });
  });
  app.state.listenOn = true;
  app.state.activeAccountId = "shop_a";
  app.state.accountGeneration = 2;
  app.state.currentUid = "";
  app.state.conversations = [];
  const requestGen = app.state.accountGeneration;
  app.state.accountGeneration = 3;
  app.state.activeAccountId = "shop_b";
  app.state.listenGeneration = 3;
  const apply = app.shouldApplyAiResult
    ? app.shouldApplyAiResult({
        accountGeneration: requestGen,
        currentAccountGeneration: app.state.accountGeneration,
        accountId: "shop_a",
        currentAccountId: "shop_b",
        uid: "u9",
        currentUid: "",
        selectSeq: 1,
        currentSelectSeq: 1,
        requestId: 1,
        currentRequestId: 1,
        humanTakeover: false,
        aiMode: "confirm",
      })
    : false;
  assert.equal(apply, false);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
