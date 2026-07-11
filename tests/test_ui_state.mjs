/**
 * UI state machine unit tests (Node.js).
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
});

test("stale qr generation rejected", () => {
  const r = UI.confirmQrLoginSuccess(
    { logged_in: true, cookie_count: 1, active_account_id: "a1" },
    { qrGeneration: 1, currentQrGeneration: 2, qrTargetAccountId: "a1" }
  );
  assert.equal(r.ok, false);
  assert.equal(r.reason, "stale_qr_generation");
});

test("session snapshot rejected when auth generation advanced", () => {
  const g = UI.shouldApplySessionSnapshot(
    { logged_in: true, accounts: [] },
    { authGeneration: 3, snapshotGeneration: 2 }
  );
  assert.equal(g.apply, false);
  assert.equal(g.reason, "stale_auth_generation");
});

test("restore trusted auth rejects stale generation", () => {
  const r = UI.canRestoreTrustedAuth(
    { generation: 1, activeAccountId: "shop_a", loggedIn: true, accounts: [] },
    { authGeneration: 2, requiredAccountId: "shop_a" }
  );
  assert.equal(r.ok, false);
});

test("restore trusted auth rejects account mismatch", () => {
  const r = UI.canRestoreTrustedAuth(
    { generation: 2, activeAccountId: "shop_a", loggedIn: true, accounts: [] },
    { authGeneration: 2, requiredAccountId: "shop_b" }
  );
  assert.equal(r.ok, false);
  assert.equal(r.reason, "account_mismatch");
});

test("A to B switch timeout must not restore A when requiredAccountId is B", () => {
  const trusted = { generation: 1, activeAccountId: "shop_a", loggedIn: true, accounts: [{ id: "shop_a" }] };
  const r = UI.canRestoreTrustedAuth(trusted, { authGeneration: 1, requiredAccountId: "shop_b" });
  assert.equal(r.ok, false);
});

test("logout success snapshot must not restore logged-in A when requiredLoggedOut", () => {
  const trusted = { generation: 2, activeAccountId: "shop_a", loggedIn: true, accounts: [] };
  const r = UI.canRestoreTrustedAuth(trusted, { authGeneration: 2, requiredAccountId: "shop_b", requiredLoggedIn: false });
  assert.equal(r.ok, false);
});

test("account switch ok:false rejected", () => {
  assert.equal(UI.validateAccountSwitchResult({ ok: false, error: "denied" }, "a2").ok, false);
});

test("account B stale context rejected after switch", () => {
  assert.equal(
    UI.shouldApplyConversationData({
      selectSeq: 1,
      currentSelectSeq: 1,
      uid: "u1",
      currentUid: "u1",
      loadGen: 1,
      currentLoadGen: 1,
      accountGeneration: 1,
      currentAccountGeneration: 2,
      accountId: "shop_a",
      currentAccountId: "shop_b",
    }),
    false
  );
});

test("AI result rejected when uid changed", () => {
  assert.equal(
    UI.shouldApplyAiResult({
      requestId: 1,
      currentRequestId: 1,
      accountGeneration: 1,
      currentAccountGeneration: 1,
      accountId: "shop_a",
      currentAccountId: "shop_a",
      uid: "buyer_a",
      currentUid: "buyer_b",
      selectSeq: 1,
      currentSelectSeq: 2,
      humanTakeover: false,
      aiMode: "auto",
    }),
    false
  );
});

test("poll events rejected when account generation changed", () => {
  assert.equal(
    UI.shouldApplyPollEvents({
      accountId: "shop_a",
      currentAccountId: "shop_a",
      accountGeneration: 1,
      currentAccountGeneration: 2,
      listenGeneration: 1,
      currentListenGeneration: 2,
    }),
    false
  );
});

test("background conv refresh timeout keeps previous data", () => {
  const r = UI.resolveConvRefreshResult(
    { timeout: true, ok: false },
    {
      requestId: 1,
      latestRequestId: 1,
      accountGeneration: 1,
      snapshotAccountGeneration: 1,
      category: "all",
      snapshotCategory: "all",
      userInitiated: false,
    }
  );
  assert.equal(r.apply, false);
  assert.equal(r.keepPrevious, true);
});

test("orders layout preserves desktop preference on resize back", () => {
  const narrow = UI.syncOrdersLayout({ wide: false, prevWide: true, desktopPrefOpen: false, drawerOpen: true });
  assert.equal(narrow.drawerOpen, false);
  const wide = UI.syncOrdersLayout({ wide: true, prevWide: false, desktopPrefOpen: false, drawerOpen: false });
  assert.equal(wide.panelOpen, false);
});

test("logout snapshot preserves buyer title fields", () => {
  const snap = UI.createWorkspaceSnapshot({
    eventSince: 1,
    eventSinceByAccount: { shop_a: 1 },
    conversations: [],
    convMeta: {},
    currentUid: "u1",
    messages: [{ text: "hi" }],
    orders: null,
    ordersLoading: false,
    ordersError: "",
    contextLoading: false,
    contextError: "",
    listenOn: true,
    listenGeneration: 1,
    loggedIn: true,
    loginPhase: "logged_in",
    activeAccountId: "shop_a",
    accountGeneration: 3,
    accounts: [],
    authStatus: "logged_in",
    authSyncError: "",
    convLastSuccess: null,
  });
  assert.equal(snap.accountGeneration, 3);
  assert.deepEqual(snap.eventSinceByAccount, { shop_a: 1 });
});

test("manual send allowed when ai paused or human takeover", () => {
  const base = {
    sendRequestId: 1,
    currentSendRequestId: 1,
    accountGeneration: 1,
    currentAccountGeneration: 1,
    accountId: "shop_a",
    currentAccountId: "shop_a",
    uid: "u1",
    currentUid: "u1",
    selectSeq: 1,
    currentSelectSeq: 1,
  };
  assert.equal(UI.shouldApplyManualSendResult(base), true);
});

test("ai auto send rejected when ai paused", () => {
  assert.equal(
    UI.shouldApplyAiAutoSendResult({
      sendRequestId: 1,
      currentSendRequestId: 1,
      accountGeneration: 1,
      currentAccountGeneration: 1,
      accountId: "shop_a",
      currentAccountId: "shop_a",
      uid: "u1",
      currentUid: "u1",
      selectSeq: 1,
      currentSelectSeq: 1,
      aiRequestId: 1,
      currentAiRequestId: 1,
      humanTakeover: false,
      aiMode: "pause",
    }),
    false
  );
});

test("ai auto send rejected when human takeover", () => {
  assert.equal(
    UI.shouldApplyAiAutoSendResult({
      sendRequestId: 1,
      currentSendRequestId: 1,
      accountGeneration: 1,
      currentAccountGeneration: 1,
      accountId: "shop_a",
      currentAccountId: "shop_a",
      uid: "u1",
      currentUid: "u1",
      selectSeq: 1,
      currentSelectSeq: 1,
      aiRequestId: 1,
      currentAiRequestId: 1,
      humanTakeover: true,
      aiMode: "auto",
    }),
    false
  );
});

test("canRestoreTrustedAuth accepts currentAuthGeneration alias", () => {
  const r = UI.canRestoreTrustedAuth(
    { generation: 2, activeAccountId: "shop_a", loggedIn: true, accounts: [] },
    { currentAuthGeneration: 2, requiredAccountId: "shop_a" }
  );
  assert.equal(r.ok, true);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
