/**
 * UI state machine unit tests (Node.js, no browser).
 * Run: node tests/test_ui_state.mjs
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const UI = require(path.join(root, "desktop/ui/app-state.js"));

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`OK ${name}`);
  } catch (e) {
    failed += 1;
    console.error(`FAIL ${name}:`, e.message);
  }
}

test("qr.done=true but logged_in=false must not succeed", () => {
  const r = UI.confirmQrLoginSuccess(
    { logged_in: false, qr: { done: true, phase: "logged_in" }, cookie_count: 3, active_account_id: "a1" },
    { qrGeneration: 1, currentQrGeneration: 1, qrTargetAccountId: "a1" }
  );
  assert.equal(r.ok, false);
});

test("logged_in=true with matching account succeeds", () => {
  const r = UI.confirmQrLoginSuccess(
    {
      logged_in: true,
      cookie_count: 2,
      active_account_id: "shop_a",
      accounts: [{ id: "shop_a", logged_in: true }],
      send_ready: true,
      listen_ready: true,
      qr: { job_id: "job1", phase: "logged_in" },
    },
    { qrGeneration: 2, currentQrGeneration: 2, qrTargetAccountId: "shop_a", qrTaskId: "job1" }
  );
  assert.equal(r.ok, true);
  assert.equal(r.uiPhase, "logged_in_ready");
});

test("stale qr generation rejected", () => {
  const r = UI.confirmQrLoginSuccess(
    { logged_in: true, cookie_count: 1, active_account_id: "a1" },
    { qrGeneration: 1, currentQrGeneration: 2, qrTargetAccountId: "a1" }
  );
  assert.equal(r.ok, false);
  assert.equal(r.reason, "stale_qr_generation");
});

test("account switch ok:false rejected", () => {
  const r = UI.validateAccountSwitchResult({ ok: false, error: "denied" }, "a2");
  assert.equal(r.ok, false);
});

test("account switch mismatch rejected", () => {
  const r = UI.validateAccountSwitchResult({ ok: true, account_id: "a1" }, "a2");
  assert.equal(r.ok, false);
});

test("account switch success when account_id matches", () => {
  const r = UI.validateAccountSwitchResult({ ok: true, account_id: "a2" }, "a2");
  assert.equal(r.ok, true);
  assert.equal(r.activeAccountId, "a2");
});

test("background conv refresh timeout keeps previous data", () => {
  const r = UI.resolveConvRefreshResult(
    { timeout: true, ok: false },
    { requestId: 1, latestRequestId: 1, accountGeneration: 1, snapshotAccountGeneration: 1, category: "all", snapshotCategory: "all", userInitiated: false }
  );
  assert.equal(r.apply, false);
  assert.equal(r.keepPrevious, true);
  assert.equal(r.showDegraded, true);
});

test("stale conv request discarded", () => {
  const r = UI.resolveConvRefreshResult(
    { ok: true, items: [{ security_user_id: "u1" }] },
    { requestId: 1, latestRequestId: 2, accountGeneration: 1, snapshotAccountGeneration: 1, category: "all", snapshotCategory: "all", userInitiated: false }
  );
  assert.equal(r.apply, false);
});

test("conversation data stale account rejected", () => {
  const ok = UI.shouldApplyConversationData({
    selectSeq: 1,
    currentSelectSeq: 1,
    uid: "u1",
    currentUid: "u1",
    loadGen: 1,
    currentLoadGen: 1,
    accountGeneration: 1,
    currentAccountGeneration: 2,
  });
  assert.equal(ok, false);
});

test("orders layout preserves desktop preference on resize back", () => {
  const narrow = UI.syncOrdersLayout({ wide: false, prevWide: true, desktopPrefOpen: false, drawerOpen: true });
  assert.equal(narrow.drawerOpen, false);
  assert.equal(narrow.desktopPrefOpen, false);
  const wide = UI.syncOrdersLayout({ wide: true, prevWide: false, desktopPrefOpen: false, drawerOpen: false });
  assert.equal(wide.panelOpen, false);
});

test("session timeout should not apply snapshot", () => {
  const g = UI.shouldApplySessionSnapshot({ timeout: true }, { authGeneration: 1, snapshotGeneration: 1 });
  assert.equal(g.apply, false);
  assert.equal(g.degraded, true);
});

test("logout snapshot can restore workspace", () => {
  const snap = UI.createWorkspaceSnapshot({
    eventSince: 3,
    conversations: [{ security_user_id: "u1" }],
    convMeta: { u1: { buyerName: "张三" } },
    currentUid: "u1",
    messages: [{ text: "hi", role: "buyer" }],
    orders: { has_order: true, cards: [] },
    ordersLoading: false,
    ordersError: "",
    contextLoading: false,
    contextError: "",
    listenOn: true,
    loggedIn: true,
    loginPhase: "logged_in",
    activeAccountId: "shop_a",
    accounts: [{ id: "shop_a", logged_in: true }],
    authStatus: "logged_in",
    convLastSuccess: null,
  });
  assert.equal(snap.currentUid, "u1");
  assert.equal(snap.conversations.length, 1);
});

test("loginBodyDelegatedAction maps logout", () => {
  assert.equal(UI.loginBodyDelegatedAction("btnLogoutShop"), "logout");
  assert.equal(UI.loginBodyDelegatedAction("btnStartQr"), "start_qr");
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
