/**
 * Pure UI state helpers for 抖店飞鸽客服工作台.
 * Browser: global PigeonUIState. Node tests: module.exports.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.PigeonUIState = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const AUTH_STATUS = {
    UNKNOWN: "unknown",
    CHECKING: "checking",
    LOGGED_OUT: "logged_out",
    LOGGING_IN: "logging_in",
    LOGGED_IN: "logged_in",
    SWITCHING: "switching",
    LOGGING_OUT: "logging_out",
    DEGRADED: "degraded",
    ERROR: "error",
  };

  function effectiveLoggedIn(j) {
    return Boolean(j && j.logged_in === true);
  }

  function isQrFlowPhase(phase) {
    return ["fetching", "waiting_scan", "scanned", "bootstrapping"].includes(String(phase || ""));
  }

  function isQrFlowActive(j) {
    const qr = (j && j.qr) || {};
    if (qr.done || qr.logged_in) return false;
    if (j && j.logged_in && (qr.phase === "bootstrapping" || qr.phase === "logged_in")) return false;
    if (qr.phase === "bootstrapping" || qr.phase === "scanned") return true;
    if (!qr.running) return false;
    return isQrFlowPhase(qr.phase);
  }

  function confirmQrLoginSuccess(j, ctx) {
    const c = ctx || {};
    if (!j || j.logged_in !== true) {
      return { ok: false, reason: "not_logged_in", uiPhase: "uncertain" };
    }
    if (c.qrGeneration != null && c.currentQrGeneration != null && c.qrGeneration !== c.currentQrGeneration) {
      return { ok: false, reason: "stale_qr_generation", uiPhase: "uncertain" };
    }
    const activeId = String(j.active_account_id || c.currentAccountId || "").trim();
    const targetId = String(c.qrTargetAccountId || activeId || "").trim();
    if (targetId && activeId && targetId !== activeId) {
      return { ok: false, reason: "account_mismatch", uiPhase: "uncertain" };
    }
    const qr = j.qr || {};
    if (c.qrTaskId && qr.job_id && String(qr.job_id) !== String(c.qrTaskId)) {
      return { ok: false, reason: "stale_qr_job", uiPhase: "uncertain" };
    }
    if (qr.phase === "expired" || qr.phase === "error") {
      return { ok: false, reason: "qr_" + qr.phase, uiPhase: qr.phase };
    }
    const accounts = Array.isArray(j.accounts) ? j.accounts : [];
    const activeRow = accounts.find((a) => String(a.id || "") === activeId);
    if (activeRow && activeRow.logged_in === false) {
      return { ok: false, reason: "active_row_not_logged_in", uiPhase: "uncertain" };
    }
    const cookieCount = Number(j.cookie_count || 0);
    if (cookieCount <= 0 && !activeRow?.logged_in) {
      return { ok: false, reason: "no_session_cookies", uiPhase: "writing_session" };
    }
    const sendReady = j.send_ready === true;
    const listenReady = j.listen_ready === true;
    let uiPhase = "logged_in_ready";
    if (!sendReady || !listenReady) {
      uiPhase = "logged_in_warming";
    }
    return { ok: true, reason: "confirmed", uiPhase, activeId, sendReady, listenReady };
  }

  function shouldApplySessionSnapshot(j, ctx) {
    const c = ctx || {};
    if (!j) return { apply: false, reason: "empty" };
    if (j.timeout === true) return { apply: false, reason: "timeout", degraded: true };
    if (j.ok === false && !j.accounts && j.logged_in === undefined) {
      return { apply: false, reason: "hard_error", degraded: true };
    }
    if (c.authGeneration != null && c.snapshotGeneration != null && c.authGeneration !== c.snapshotGeneration) {
      return { apply: false, reason: "stale_auth_generation" };
    }
    return { apply: true, reason: "ok" };
  }

  function canRestoreTrustedAuth(trusted, ctx) {
    const c = ctx || {};
    const t = trusted;
    if (!t) return { ok: false, reason: "empty" };
    if (c.authGeneration != null && t.generation != null && t.generation !== c.authGeneration) {
      return { ok: false, reason: "stale_auth_generation" };
    }
    if (c.requiredAccountId != null && String(t.activeAccountId || "") !== String(c.requiredAccountId || "")) {
      return { ok: false, reason: "account_mismatch" };
    }
    if (c.requiredLoggedIn === true && !t.loggedIn) {
      return { ok: false, reason: "logged_out_snapshot" };
    }
    if (c.requiredLoggedIn === false && t.loggedIn) {
      return { ok: false, reason: "logged_in_snapshot" };
    }
    return { ok: true, reason: "ok" };
  }

  function validateAccountSwitchResult(j, targetAccountId) {
    const target = String(targetAccountId || "").trim();
    if (!target) return { ok: false, error: "缺少目标账号" };
    if (!j) return { ok: false, error: "无响应" };
    if (j.timeout === true) return { ok: false, error: "切换超时，请重试" };
    if (j.ok === false) return { ok: false, error: j.error || "切换失败" };
    const active = String(j.active_account_id || j.account_id || "").trim();
    if (!active) return { ok: false, error: "后端未返回活动账号" };
    if (active !== target) return { ok: false, error: "账号切换未生效" };
    return { ok: true, activeAccountId: active };
  }

  function validateLogoutResult(j) {
    if (!j) return { ok: false, error: "无响应" };
    if (j.timeout === true) return { ok: false, error: "退出超时，请重试" };
    if (j.ok === false) return { ok: false, error: j.error || "退出失败" };
    return {
      ok: true,
      activeAccountId: String(j.active_account_id || j.switched_to || "").trim(),
      loggedIn: Boolean(j.logged_in),
      switchedTo: String(j.switched_to || "").trim(),
    };
  }

  function shouldReplaceConvList(ctx) {
    const c = ctx || {};
    if (c.requestId != null && c.latestRequestId != null && c.requestId !== c.latestRequestId) {
      return { replace: false, reason: "stale_request" };
    }
    if (
      c.accountGeneration != null &&
      c.snapshotAccountGeneration != null &&
      c.accountGeneration !== c.snapshotAccountGeneration
    ) {
      return { replace: false, reason: "stale_account" };
    }
    if (c.category != null && c.snapshotCategory != null && c.category !== c.snapshotCategory) {
      return { replace: false, reason: "stale_category" };
    }
    return { replace: true, reason: "ok" };
  }

  function resolveConvRefreshResult(j, ctx) {
    const c = ctx || {};
    const gate = shouldReplaceConvList(c);
    if (!gate.replace) {
      return { apply: false, reason: gate.reason, keepPrevious: true };
    }
    if (j && j.timeout === true) {
      return { apply: false, reason: "timeout", keepPrevious: true, showDegraded: true };
    }
    if (j && j.ok === false && !(j.items || []).length) {
      return { apply: false, reason: j.error || "fetch_failed", keepPrevious: true, showError: Boolean(c.userInitiated) };
    }
    const items = Array.isArray(j?.items) ? j.items : [];
    const explicitEmpty = j && j.ok !== false;
    return { apply: true, items, explicitEmpty, reason: "ok" };
  }

  function shouldApplyConversationData(ctx) {
    const c = ctx || {};
    if (c.selectSeq !== c.currentSelectSeq) return false;
    if (c.uid !== c.currentUid) return false;
    if (c.loadGen != null && c.currentLoadGen != null && c.loadGen !== c.currentLoadGen) return false;
    if (c.accountGeneration !== c.currentAccountGeneration) return false;
    if (c.accountId != null && c.currentAccountId != null && c.accountId !== c.currentAccountId) return false;
    return true;
  }

  function shouldApplyAiResult(ctx) {
    const c = ctx || {};
    if (c.requestId != null && c.currentRequestId != null && c.requestId !== c.currentRequestId) return false;
    if (c.accountGeneration !== c.currentAccountGeneration) return false;
    if (c.accountId !== c.currentAccountId) return false;
    if (c.uid !== c.currentUid) return false;
    if (c.selectSeq !== c.currentSelectSeq) return false;
    if (c.humanTakeover) return false;
    if (c.aiMode === "pause") return false;
    return true;
  }

  function shouldApplySendResult(ctx) {
    return shouldApplyAiResult(ctx);
  }

  function shouldApplyPollEvents(ctx) {
    const c = ctx || {};
    if (c.accountId !== c.currentAccountId) return false;
    if (c.accountGeneration !== c.currentAccountGeneration) return false;
    if (c.listenGeneration !== c.currentListenGeneration) return false;
    return true;
  }

  function aiDraftKey(accountId, uid) {
    return `${String(accountId || "")}:${String(uid || "")}`;
  }

  function syncOrdersLayout(ctx) {
    const c = ctx || {};
    const wide = Boolean(c.wide);
    const prevWide = Boolean(c.prevWide);
    const desktopPrefOpen = c.desktopPrefOpen !== false;
    const drawerOpen = Boolean(c.drawerOpen);

    if (wide !== prevWide) {
      if (wide) {
        return { desktopPrefOpen, drawerOpen: false, panelOpen: desktopPrefOpen, wide };
      }
      return { desktopPrefOpen, drawerOpen: false, panelOpen: false, wide };
    }
    if (wide) {
      return { desktopPrefOpen, drawerOpen: false, panelOpen: desktopPrefOpen, wide };
    }
    return { desktopPrefOpen, drawerOpen, panelOpen: drawerOpen, wide };
  }

  function createWorkspaceSnapshot(state) {
    return {
      eventSince: state.eventSince,
      eventSinceByAccount: { ...(state.eventSinceByAccount || {}) },
      conversations: (state.conversations || []).slice(),
      convMeta: JSON.parse(JSON.stringify(state.convMeta || {})),
      currentUid: state.currentUid || "",
      messages: (state.messages || []).slice(),
      orders: state.orders ? JSON.parse(JSON.stringify(state.orders)) : null,
      ordersLoading: state.ordersLoading,
      ordersError: state.ordersError || "",
      contextLoading: state.contextLoading,
      contextError: state.contextError || "",
      listenOn: state.listenOn,
      listenGeneration: state.listenGeneration || 0,
      loggedIn: state.loggedIn,
      loginPhase: state.loginPhase,
      activeAccountId: state.activeAccountId || "",
      accountGeneration: state.accountGeneration || 0,
      accounts: (state.accounts || []).slice(),
      accountSelectValue: state.activeAccountId || "",
      authStatus: state.authStatus,
      authSyncError: state.authSyncError || "",
      aiDraft: state.aiDraft || "",
      aiIntent: state.aiIntent || "",
      aiState: state.aiState || "idle",
      composerValue: state.composerValue || "",
      humanTakeover: state.humanTakeover,
      buyerTitle: state.buyerTitle || "",
      buyerAvatar: state.buyerAvatar || "",
      buyerMeta: state.buyerMeta || "",
      convLastSuccess: state.convLastSuccess
        ? { ...state.convLastSuccess, items: (state.convLastSuccess.items || []).slice() }
        : null,
    };
  }

  function loginBodyDelegatedAction(buttonId) {
    const map = {
      btnStartQr: "start_qr",
      btnRefreshQr: "refresh_qr",
      btnReQrLogin: "start_qr",
      btnStartCdp: "start_cdp",
      btnReOnboard: "start_cdp",
      btnRenewSession: "renew_session",
      btnWarmInners: "warm_inners",
      btnLogoutShop: "logout",
    };
    return map[buttonId] || null;
  }

  return {
    AUTH_STATUS,
    effectiveLoggedIn,
    isQrFlowActive,
    isQrFlowPhase,
    confirmQrLoginSuccess,
    shouldApplySessionSnapshot,
    canRestoreTrustedAuth,
    validateAccountSwitchResult,
    validateLogoutResult,
    shouldReplaceConvList,
    resolveConvRefreshResult,
    shouldApplyConversationData,
    shouldApplyAiResult,
    shouldApplySendResult,
    shouldApplyPollEvents,
    aiDraftKey,
    syncOrdersLayout,
    createWorkspaceSnapshot,
    loginBodyDelegatedAction,
  };
});
