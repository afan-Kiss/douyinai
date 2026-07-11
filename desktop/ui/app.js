/* 抖店 AI 客服工作台 — 前端（对接现有 /api/*，不改后端业务结构） */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const FETCH_CONV_CATEGORIES = new Set(["all", "recent"]);

  function convCategoryParam(cat) {
    if (cat === "recent") return "recent";
    if (cat === "all") return "all";
    return "";
  }

  function convListEmptyHint(cat) {
    if (cat === "recent") return "暂无最近联系<br/><span class=\"muted\">点击后将自动拉取联系人</span>";
    return "暂无会话<br/><span class=\"muted\">登录后会自动拉取联系人</span>";
  }

  const CATEGORIES = [
    { id: "all", label: "当前会话" },
    { id: "recent", label: "最近联系" },
    { id: "no_reply", label: "未回复" },
    { id: "ai_replied", label: "AI 已回复" },
    { id: "has_order", label: "有订单" },
    { id: "risk", label: "售后关注" },
  ];

  const INTENT_LABELS = {
    ask_price: "问价",
    urge_ship: "催发货",
    after_sale: "售后",
    bargain: "砍价",
    ask_image: "要图片",
    ask_material: "问材质",
    other: "其他",
  };

  const UI = () => (typeof PigeonUIState !== "undefined" ? PigeonUIState : {});

  const state = {
    loggedIn: false,
    loginPhase: "checking",
    authStatus: "unknown",
    authGeneration: 0,
    authPendingAction: null,
    authLastTrusted: null,
    authSyncError: "",
    conversations: [],
    convMeta: {},
    convListStatus: "idle",
    convListRequestId: 0,
    convLastSuccess: null,
    activeCategory: "all",
    currentUid: "",
    messages: [],
    orders: null,
    ordersLoading: false,
    ordersError: "",
    contextLoading: false,
    contextError: "",
    listenOn: false,
    eventSince: 0,
    aiMode: "confirm",
    aiState: "idle",
    aiDraft: "",
    aiIntent: "",
    humanTakeover: false,
    markedHuman: new Set(),
    riskWatch: new Set(),
    desktopOrdersPrefOpen: true,
    ordersDrawerOpen: false,
    accounts: [],
    activeAccountId: "",
    accountGeneration: 0,
    lastSession: null,
    qrPollingActive: false,
    qrStartedAt: 0,
    lastQrRefreshAt: 0,
    qrTaskId: "",
    qrGeneration: 0,
    qrTargetAccountId: "",
    enrichInFlight: new Set(),
    enrichFailedAt: {},
  };
  let _progressCount = 0;
  let _progressTimer = null;
  let _selectReqSeq = 0;
  let _contextLoadGen = 0;
  let _ordersLoadGen = 0;
  let _ordersWideLayout = window.innerWidth > 1100;
  let _convAbortController = null;
  let _authRefreshGen = 0;
  const actionLocks = {
    qr: { busy: false, token: 0 },
    switch: { busy: false, token: 0 },
    logout: { busy: false, token: 0 },
  };

  function tryAcquireLock(kind) {
    const lock = actionLocks[kind];
    if (!lock || lock.busy) return null;
    lock.busy = true;
    lock.token += 1;
    return lock.token;
  }

  function releaseLock(kind, token) {
    const lock = actionLocks[kind];
    if (!lock || lock.token !== token) return;
    lock.busy = false;
  }

  function isLockBusy(kind) {
    return Boolean(actionLocks[kind]?.busy);
  }

  function bumpAuthGeneration() {
    state.authGeneration += 1;
    return state.authGeneration;
  }

  function bumpAccountGeneration() {
    state.accountGeneration += 1;
    _selectReqSeq += 1;
    _contextLoadGen += 1;
    _ordersLoadGen += 1;
    if (_convAbortController) {
      _convAbortController.abort();
      _convAbortController = null;
    }
    state.enrichInFlight.clear();
    return state.accountGeneration;
  }

  function rememberTrustedAuth(j) {
    if (!j || j.timeout || (j.ok === false && j.logged_in === undefined && !j.accounts)) return;
    state.authLastTrusted = {
      loggedIn: effectiveLoggedIn(j),
      activeAccountId: String(j.active_account_id || state.activeAccountId || ""),
      accounts: (j.accounts || []).slice(),
      shopName: j.shop_name || "",
      at: Date.now(),
      generation: state.authGeneration,
    };
    state.authSyncError = "";
    if (state.authStatus === UI().AUTH_STATUS?.DEGRADED) {
      state.authStatus = state.authLastTrusted.loggedIn
        ? UI().AUTH_STATUS?.LOGGED_IN || "logged_in"
        : UI().AUTH_STATUS?.LOGGED_OUT || "logged_out";
    }
  }

  function restoreTrustedAuth() {
    const t = state.authLastTrusted;
    if (!t) return false;
    state.loggedIn = Boolean(t.loggedIn);
    state.activeAccountId = t.activeAccountId || state.activeAccountId;
    state.accounts = (t.accounts || []).slice();
    state.authStatus = t.loggedIn
      ? UI().AUTH_STATUS?.LOGGED_IN || "logged_in"
      : UI().AUTH_STATUS?.LOGGED_OUT || "logged_out";
    updateAuthChrome();
    renderAccountPicker();
    return true;
  }

  function setAuthSyncDegraded(message) {
    state.authStatus = UI().AUTH_STATUS?.DEGRADED || "degraded";
    state.authSyncError = message || "状态同步异常，正在重试";
    updateAuthChrome();
    renderAuthSyncBanner();
  }

  function renderAuthSyncBanner() {
    const card = $("loginCard");
    if (!card) return;
    let banner = $("authSyncBanner");
    if (!state.authSyncError) {
      if (banner) banner.remove();
      return;
    }
    if (!banner) {
      banner = document.createElement("div");
      banner.id = "authSyncBanner";
      banner.className = "status-err auth-sync-banner";
      card.insertBefore(banner, $("loginBody"));
    }
    banner.textContent = state.authSyncError;
  }

  function showGlobalProgress() {
    _progressCount += 1;
    const el = $("globalProgress");
    if (!el) return;
    el.hidden = false;
    el.classList.remove("done");
    clearTimeout(_progressTimer);
  }

  function hideGlobalProgress() {
    _progressCount = Math.max(0, _progressCount - 1);
    if (_progressCount > 0) return;
    const el = $("globalProgress");
    if (!el) return;
    el.classList.add("done");
    _progressTimer = setTimeout(() => {
      el.hidden = true;
      el.classList.remove("done");
    }, 420);
  }

  function withBtnLoading(btn, fn) {
    if (!btn) return fn();
    btn.classList.add("loading");
    btn.disabled = true;
    return Promise.resolve(fn()).finally(() => {
      btn.classList.remove("loading");
      btn.disabled = false;
    });
  }

  const BG_API = { trackProgress: false };
  const CS_MODE = true;

  function resetWorkspaceState() {
    state.eventSince = 0;
    state.conversations = [];
    state.convMeta = {};
    state.currentUid = "";
    state.messages = [];
    state.orders = null;
    state.ordersLoading = false;
    state.ordersError = "";
    state.contextLoading = false;
    state.contextError = "";
    state.aiDraft = "";
    state.aiIntent = "";
    state.aiState = "idle";
    state.humanTakeover = false;
    state.markedHuman = new Set();
    state.riskWatch = new Set();
    $("buyerTitle").textContent = "选择左侧会话";
    $("buyerAvatar").textContent = "客";
    $("buyerMeta").textContent = "暂无选中买家";
    $("msgEmpty").hidden = false;
    $("messageList").hidden = true;
    $("messageList").innerHTML = "";
    $("composerInput").value = "";
    $("aiDraft").hidden = true;
    setAiState("idle", "选中会话后，AI 会结合聊天记录和订单信息整理回复。");
    $("buyerOverviewBody").innerHTML = '<div class="empty-mini muted">选中会话后显示买家信息</div>';
    $("orderBody").innerHTML = '<div class="empty-mini muted">选中会话后显示订单</div>';
    $("drawerOrderBody").innerHTML = '<div class="empty-mini muted">选中会话后显示订单</div>';
    $("insightBody").innerHTML = '<div class="empty-mini muted">AI 会识别买家意图并给出回复建议</div>';
    $("convCount").textContent = "—";
    $("convList").innerHTML = '<div class="empty-mini muted">切换账号后请刷新会话列表</div>';
  }

  async function api(path, opt = {}) {
    const track = opt.trackProgress === true;
    const timeoutMs = Number(opt.timeoutMs || 8000);
    const throwOnError = opt.throwOnError;
    const externalSignal = opt.signal;
    const {
      trackProgress: _trackProgress,
      timeoutMs: _timeoutMs,
      throwOnError: _throwOnError,
      signal: _extSignal,
      headers: extraHeaders,
      ...fetchRest
    } = opt;

    if (track) showGlobalProgress();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    if (externalSignal) {
      if (externalSignal.aborted) {
        controller.abort();
      } else {
        externalSignal.addEventListener("abort", () => controller.abort(), { once: true });
      }
    }

    try {
      const r = await fetch(path, {
        ...fetchRest,
        headers: { "Content-Type": "application/json", ...(extraHeaders || {}) },
        signal: controller.signal,
      });
      const ct = r.headers.get("content-type") || "";
      let data = {};
      if (ct.includes("application/json")) {
        data = await r.json();
      } else {
        const text = await r.text();
        if (!r.ok) {
          throw new Error(text.slice(0, 120) || `HTTP ${r.status}`);
        }
        return { ok: true, raw: text };
      }
      if (!r.ok) {
        const err = data.error || data.message || data.reason || `HTTP ${r.status}`;
        return { ok: false, error: err, status: r.status, ...data };
      }
      return data;
    } catch (e) {
      if (e && e.name === "AbortError") {
        if (throwOnError) throw e;
        return { ok: false, timeout: true, error: "请求超时" };
      }
      if (throwOnError) throw e;
      return { ok: false, error: e.message || String(e) };
    } finally {
      clearTimeout(timer);
      if (track) hideGlobalProgress();
    }
  }

  function toast(msg, ms = 3200) {
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = msg;
    $("toastHost").appendChild(el);
    setTimeout(() => el.remove(), ms);
  }

  function promptFilePath(title, example) {
    return window.prompt(title, example || "");
  }

  async function sessionDoctor() {
    return api("/api/session/doctor", {
      method: "POST",
      body: JSON.stringify({ fix: true }),
      trackProgress: true,
    });
  }

  async function startListening() {
    const j = await api("/api/listen/start", { method: "POST", ...BG_API });
    state.listenOn = j.running !== false && j.ok !== false;
    if (!state.listenOn) {
      await syncListenStatus();
    }
    return j;
  }

  async function syncListenStatus() {
    const j = await api("/api/listen/status", BG_API);
    state.listenOn = Boolean(j.running);
    return j;
  }

  function setConn(ok, text) {
    const pill = $("connPill");
    pill.className = "pill" + (ok ? " ready" : " offline");
    pill.innerHTML = `<span class="dot${ok ? " pulse" : ""}"></span>${text}`;
  }

  async function refreshProtocolStatus() {
    const pill = $("protoPill");
    if (!pill) return;
    if (CS_MODE) {
      pill.hidden = true;
      return;
    }
    try {
      const j = await api("/api/protocol/status", BG_API);
      const snap = j.conv_snapshot ? "快照✓" : "快照—";
      const ws = j.has_ws ? "WS✓" : "WS—";
      const send = j.send_ready ? "发信✓" : "发信—";
      const ok = j.foundation_ok ? "就绪" : "预热";
      const action = j.recommended_action && j.recommended_action !== "ready" ? ` · ${j.recommended_action}` : "";
      pill.textContent = `协议 ${ok} · ${ws} · ${send} · ${snap}${action}`;
      pill.className = "pill muted" + (j.foundation_ok && j.send_ready ? "" : " offline");
      pill.title = (j.blockers || []).join("\n") || pill.textContent;
    } catch {
      pill.textContent = "协议 —";
    }
  }

  async function exportSessionPack() {
    toast("正在导出会话包…");
    const j = await api("/api/session-pack/export", { method: "POST", body: "{}" });
    if (!j.ok) {
      toast("导出失败: " + (j.error || "未知错误"));
      return;
    }
    toast("会话包已导出: " + (j.path || "accounts/当前账号/pigeon_session_pack.zip"));
  }

  async function importSessionPackPath(path, setActive = false) {
    if (!path) return;
    toast("正在导入会话包…");
    const j = await api("/api/session-pack/import", {
      method: "POST",
      body: JSON.stringify({ path: path.trim(), set_active: setActive }),
    });
    const res = j.result || j;
    if (res.ok === false && !res.ready) {
      toast("导入失败: " + (res.error || j.error || "未知错误"));
      return;
    }
    toast(res.send_ready ? "会话包导入成功，发信已就绪" : "会话包已导入（发信可能未就绪，见协议状态）");
    await refreshProtocolStatus();
    await refreshLogin();
    if (res.recommended_action === "cdp_warm_inners") {
      toast("backstage 有效，正在后台预热 169B…", 4000);
    } else if (res.needs_cdp_onboard || res.recommended_action === "cdp_onboard") {
      toast("会话需浏览器登录飞鸽才能发信", 5000);
    }
    await refreshConversations();
    if (res.listen_ready || res.ready) {
      await startListening();
    }
  }

  async function importHarPath(path) {
    if (!path) return;
    toast("正在导入 HAR…");
    const j = await api("/api/import-har", {
      method: "POST",
      body: JSON.stringify({ path, replace: false }),
    });
    if (j.ok === false && j.error) {
      toast("导入失败: " + j.error);
      return;
    }
    if (j.result && j.result.error) {
      toast("导入失败: " + j.result.error);
      return;
    }
    toast("HAR 导入成功，正在预热协议");
    await sessionDoctor();
    await refreshProtocolStatus();
    await refreshLogin();
  }

  function cleanBuyerName(name) {
    if (name == null) return "";
    const s = String(name).trim();
    if (!s) return "";
    const lower = s.toLowerCase();
    const bad = new Set([
      "其他", "未知", "未知买家", "站内push推送", "站内push", "抖音",
      "今日头条", "火山", "来源", "用户", "买家", "暂无", "null", "none", "undefined",
    ]);
    if (bad.has(s) || bad.has(lower)) return "";
    if (/fallback|xundan\s*11001|已知买家\s*[（(]/i.test(s)) return "";
    if (lower.includes("fallback")) return "";
    return s;
  }

  function convPreview(c) {
    const raw = String(c.preview || "").trim();
    if (!raw) return "暂无消息";
    if (/已知买家\s*[（(].*fallback/i.test(raw)) return "已知买家";
    if (/xundan\s*11001\s*fallback/i.test(raw)) return "已知买家";
    if (/fallback/i.test(raw) && raw.includes("已知买家")) return "已知买家";
    return raw;
  }

  function isUidFallbackName(name, uid) {
    const n = String(name || "").trim().replace(/\s+/g, "");
    if (!n) return false;
    if (/^买家[A-Za-z0-9_-]{4,10}$/.test(n)) return true;
    const u = String(uid || "");
    if (u && n === `买家${u.slice(-6)}`) return true;
    return false;
  }

  function convName(c) {
    const uid = String(c.security_user_id || "");
    const meta = state.convMeta[uid] || {};
    if (meta.buyerName && !isUidFallbackName(meta.buyerName, uid)) return meta.buyerName;
    const candidates = [
      c.display_name,
      c.buyer_name,
      c.name,
      c.nickname,
      c.nick_name,
      c.user_name,
    ];
    for (const v of candidates) {
      const cleaned = cleanBuyerName(v);
      if (cleaned && !isUidFallbackName(cleaned, uid)) return cleaned;
    }
    return uid ? `买家${uid.slice(-6)}` : "未知买家";
  }

  function avatarChar(name) {
    return (name || "客").trim().charAt(0) || "客";
  }

  function isBuyerRole(role) {
    const r = (role || "").toLowerCase();
    return r === "buyer" || r === "customer" || r === "user";
  }

  function isServiceRole(role) {
    const r = (role || "").toLowerCase();
    return r === "service" || r === "ai" || r === "shop" || r === "merchant";
  }

  /* ——— 登录 ——— */
  const ONBOARD_PHASES = {
    idle: ["未登录", "点击「浏览器登录」打开飞鸽工作台扫码（推荐）"],
    starting: ["正在启动", "准备浏览器登录流程…"],
    launching: ["正在打开浏览器", "Chrome 将打开 im.jinritemai.com，请扫码登录"],
    waiting_login: ["等待扫码", "请在 Chrome 窗口用抖音/抖店 App 扫码登录飞鸽"],
    syncing: ["同步会话", "正在同步 Cookie 与 backstage…"],
    warming: ["预热发信", "正在捕获 169B 发信密钥（约 30 秒）…"],
    done: ["登录完成", "协议已就绪"],
    error: ["登录失败", ""],
    logging_out: ["正在退出当前店铺", "请稍候，正在清理登录状态…"],
  };

  let _lastLoginRenderKey = "";
  let _qrPollGen = 0;

  function loginViewKey(j) {
    const qr = j.qr || {};
    const onboard = j.onboard || {};
    const qrActive = isQrFlowActive(j);
    const onboardBusy = onboard.running && onboard.phase !== "idle" && onboard.phase !== "done";
    if (effectiveLoggedIn(j) && !qrActive && !onboardBusy) {
      return `logged:${j.send_ready}:${j.needs_renew}:${j.backstage_ok}:${(j.blockers || []).join(";")}:${j.shop_name || ""}`;
    }
    const phase = onboardBusy ? onboard.phase || "starting" : qr.phase || "logged_out";
    return `flow:${phase}:${qr.running}:${qr.error || ""}:${onboard.error || ""}`;
  }

  function stopQrPoll() {
    _qrPollGen += 1;
    state.qrPollingActive = false;
    state.qrImgSrc = "";
    stopQrImgRefresh();
  }

  function isActiveAccountLoggedIn() {
    if (state.loggedIn) return true;
    const row = (state.accounts || []).find((a) => a.id === state.activeAccountId);
    return Boolean(row?.logged_in);
  }

  function effectiveLoggedIn(j) {
    const fn = UI().effectiveLoggedIn;
    return fn ? fn(j) : Boolean(j?.logged_in === true);
  }

  function isQrFlowActive(j) {
    const fn = UI().isQrFlowActive;
    return fn ? fn(j) : false;
  }

  function updateAuthChrome() {
    const loggedIn = Boolean(state.loggedIn);
    document.body.classList.toggle("auth-logged-in", loggedIn);
    document.body.classList.toggle("auth-logged-out", !loggedIn);
    document.body.classList.toggle("auth-degraded", state.authStatus === (UI().AUTH_STATUS?.DEGRADED || "degraded"));
    const btnLogout = $("btnLogoutAccount");
    if (btnLogout) {
      btnLogout.hidden = !loggedIn;
      btnLogout.disabled = isLockBusy("logout") || isLockBusy("switch");
    }
    const btnAdd = $("btnAddAccount");
    if (btnAdd) btnAdd.disabled = isLockBusy("logout") || isLockBusy("switch") || isLockBusy("qr");
    const sel = $("accountSelect");
    if (sel) sel.disabled = isLockBusy("logout") || isLockBusy("switch");
    const btnOrders = $("btnToggleOrders");
    if (btnOrders) {
      btnOrders.disabled = !loggedIn;
      btnOrders.title = loggedIn ? "订单侧栏" : "登录后可查看订单";
    }
    const loginCard = $("loginCard");
    if (loginCard) {
      loginCard.classList.toggle("login-card--logged-in", loggedIn);
      loginCard.classList.toggle("login-card--logged-out", !loggedIn);
    }
    document.querySelector(".workspace")?.classList.toggle("workspace--guest", !loggedIn);
    renderAuthSyncBanner();
  }

  function syncLoginState(j, { trust = true } = {}) {
    if (trust) {
      state.lastSession = j;
      state.accounts = j.accounts || [];
      state.activeAccountId = j.active_account_id || "";
    }
    const activeRow = state.accounts.find((a) => a.id === state.activeAccountId);
    if (state.qrPollingActive) {
      const qr = j.qr || {};
      const qrDone = Boolean(qr.done || qr.phase === "logged_in");
      state.loggedIn = qrDone && Boolean(j.logged_in);
    } else {
      state.loggedIn = effectiveLoggedIn(j);
    }
    const onboard = j.onboard || {};
    state.loginPhase =
      onboard.phase && onboard.phase !== "idle"
        ? onboard.phase
        : j.qr?.phase || (state.loggedIn ? "logged_in" : "logged_out");
    if (state.authPendingAction === "logging_out") {
      state.authStatus = UI().AUTH_STATUS?.LOGGING_OUT || "logging_out";
    } else if (state.authPendingAction === "switching") {
      state.authStatus = UI().AUTH_STATUS?.SWITCHING || "switching";
    } else if (state.qrPollingActive) {
      state.authStatus = UI().AUTH_STATUS?.LOGGING_IN || "logging_in";
    } else if (state.loggedIn) {
      state.authStatus = UI().AUTH_STATUS?.LOGGED_IN || "logged_in";
    } else {
      state.authStatus = UI().AUTH_STATUS?.LOGGED_OUT || "logged_out";
    }
    if (trust) rememberTrustedAuth(j);
    updateAuthChrome();
    return { activeRow };
  }

  async function refreshLogin(forceConv = false) {
    const reqGen = ++_authRefreshGen;
    const snapshotGen = state.authGeneration;
    if (state.authStatus !== (UI().AUTH_STATUS?.LOGGING_OUT || "logging_out") &&
        state.authStatus !== (UI().AUTH_STATUS?.SWITCHING || "switching")) {
      state.authStatus = UI().AUTH_STATUS?.CHECKING || "checking";
    }
    try {
      const j = await api("/api/session?light=1", BG_API);
      if (reqGen !== _authRefreshGen) return;
      const gate = UI().shouldApplySessionSnapshot
        ? UI().shouldApplySessionSnapshot(j, { authGeneration: snapshotGen, snapshotGeneration: snapshotGen })
        : { apply: !(j.timeout || (j.ok === false && !j.accounts && j.logged_in === undefined)), degraded: Boolean(j.timeout) };
      if (!gate.apply) {
        if (gate.degraded) {
          setAuthSyncDegraded(j.timeout ? "状态同步超时，保留上次登录状态" : "状态同步失败，保留上次登录状态");
          restoreTrustedAuth();
          if (state.authLastTrusted) {
            renderLogin(state.lastSession || { logged_in: state.authLastTrusted.loggedIn, accounts: state.authLastTrusted.accounts });
          }
        }
        return;
      }
      const { activeRow } = syncLoginState(j);
      if (state.qrPollingActive && !state.loggedIn) {
        renderAccountPicker();
        return;
      }
      const loggedInRows = state.accounts.filter((a) => a.logged_in);
      if (
        !state.qrPollingActive &&
        !state.authPendingAction &&
        !activeRow?.logged_in &&
        loggedInRows.length === 1 &&
        loggedInRows[0].id !== state.activeAccountId
      ) {
        toast(`检测到已登录 ${accountPickerLabel(loggedInRows[0])}，正在切换…`);
        await switchAccount(loggedInRows[0].id);
        return;
      }
      renderAccountPicker();
      renderLogin(j);
      if (state.loggedIn) {
        if (forceConv || state.conversations.length === 0 || state.activeCategory === "recent") {
          await refreshConversations(forceConv, state.activeCategory || "recent", { heavy: false });
        }
        await syncListenStatus();
        if (!state.listenOn && j.listen_ready !== false && !isLockBusy("logout") && !isLockBusy("switch")) {
          await startListening();
        }
      }
    } catch {
      if (reqGen !== _authRefreshGen) return;
      if (restoreTrustedAuth()) {
        setAuthSyncDegraded("无法连接后端，保留上次登录状态");
        renderLogin(state.lastSession || { logged_in: state.authLastTrusted?.loggedIn, accounts: state.authLastTrusted?.accounts || [] });
      } else {
        state.authStatus = UI().AUTH_STATUS?.ERROR || "error";
        renderLogin({ logged_in: false, qr: { phase: "error", error: "无法连接后端" } });
      }
    }
  }

  function accountPickerLabel(a) {
    const shop = String(a.shop_id || "").trim();
    const shopName = String(a.shop_name || "").trim();
    const label = String(a.label || "").trim();
    const looksLikeId = (v) =>
      !v ||
      v === shop ||
      /^店铺\s*\d+$/.test(v) ||
      /^shop_\d+$/i.test(v) ||
      /^acct_[0-9a-f]+$/i.test(v);
    const sessionName = String(state.lastSession?.shop_name || "").trim();
    const isActiveLoggedIn =
      a.logged_in && String(a.id || "") === String(state.activeAccountId || "");
    if (isActiveLoggedIn && sessionName && !looksLikeId(sessionName) && sessionName !== "飞鸽客服") {
      return sessionName;
    }
    if (a.is_empty_slot && !a.logged_in) return "扫码登录新店铺";
    if (!a.logged_in && label === "扫码登录新店铺") return "扫码登录新店铺";
    if (shopName && !looksLikeId(shopName)) return shopName;
    if (label && label !== "空账号槽" && !looksLikeId(label) && label !== "扫码登录新店铺") return label;
    if (shop && !looksLikeId(shop)) return shop;
    if (a.logged_in) return sessionName && sessionName !== "飞鸽客服" ? sessionName : shop || label || "已登录店铺";
    return "扫码登录新店铺";
  }

  function renderAccountPicker() {
    const sel = $("accountSelect");
    if (!sel) return;
    const rows = state.accounts || [];
    const active = state.activeAccountId || "";
    sel.replaceChildren();
    if (!rows.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "未配置账号";
      sel.appendChild(opt);
      return;
    }
    const loggedIn = rows.filter((a) => a.logged_in && !a.is_empty_slot);
    let emptySlot = rows.find((a) => a.is_empty_slot || !a.logged_in);
    const sorted = [];
    const activeRow = loggedIn.find((a) => a.id === active);
    if (activeRow) sorted.push(activeRow);
    loggedIn.filter((a) => a.id !== active).forEach((a) => sorted.push(a));
    if (!emptySlot) {
      emptySlot = { id: "__new_shop__", is_empty_slot: true, logged_in: false };
    }
    if (!sorted.some((a) => a.id === emptySlot.id)) sorted.push(emptySlot);
    for (const a of sorted) {
      const opt = document.createElement("option");
      opt.value = a.id === "__new_shop__" ? "" : a.id;
      const name = accountPickerLabel(a);
      if (a.id === active && (a.logged_in || isActiveAccountLoggedIn())) {
        opt.textContent = `当前 · ${name}`;
      } else if (a.logged_in) {
        opt.textContent = name;
      } else {
        opt.textContent = "+ 扫码登录新店铺";
      }
      if (a.id === active) opt.selected = true;
      sel.appendChild(opt);
    }
  }

  async function switchAccount(accountId, opts = {}) {
    if (!accountId || isLockBusy("switch") || isLockBusy("logout")) return;
    const preserveQr = Boolean(opts.preserveQr);
    const same = accountId === state.activeAccountId;
    if (same && !preserveQr) {
      toast("正在刷新当前账号…");
      state.eventSince = 0;
      await refreshLogin(false);
      if (isActiveAccountLoggedIn()) {
        await refreshConversations(true, state.activeCategory || "recent", { heavy: false });
      }
      return;
    }
    if (same) return;

    const lockToken = tryAcquireLock("switch");
    if (lockToken == null) return;

    const snapshot = UI().createWorkspaceSnapshot ? UI().createWorkspaceSnapshot(state) : null;
    const prevAccountId = state.activeAccountId;
    const sel = $("accountSelect");
    if (sel) sel.value = prevAccountId;

    state.authPendingAction = "switching";
    bumpAuthGeneration();
    if (!preserveQr) toast("正在切换账号…");
    if (!preserveQr) {
      resetWorkspaceState();
      state.listenOn = false;
    }
    updateAuthChrome();

    try {
      const j = await api("/api/accounts/switch", {
        method: "POST",
        body: JSON.stringify({ account_id: accountId, restart_listen: !preserveQr }),
        trackProgress: !preserveQr,
      });
      if (lockToken !== actionLocks.switch.token) return;
      const valid = UI().validateAccountSwitchResult
        ? UI().validateAccountSwitchResult(j, accountId)
        : { ok: j.ok !== false && String(j.active_account_id || j.account_id || "") === accountId, error: j.error };
      if (!valid.ok) {
        if (snapshot) restoreWorkspaceSnapshot(snapshot);
        state.activeAccountId = prevAccountId;
        if (sel) sel.value = prevAccountId;
        renderAccountPicker();
        toast("切换失败: " + (valid.error || "请重试"));
        await refreshLogin(false);
        return;
      }
      if (!preserveQr) {
        state.eventSince = 0;
        state.activeAccountId = valid.activeAccountId || accountId;
        await refreshLogin(false);
        await refreshConversations(true, state.activeCategory || "recent", { heavy: false });
        toast("已切换账号");
      } else {
        state.activeAccountId = valid.activeAccountId || accountId;
        renderAccountPicker();
      }
    } catch (e) {
      if (lockToken !== actionLocks.switch.token) return;
      if (snapshot) restoreWorkspaceSnapshot(snapshot);
      state.activeAccountId = prevAccountId;
      if (sel) sel.value = prevAccountId;
      renderAccountPicker();
      toast("切换失败: " + (e.message || e));
      await refreshLogin(false);
    } finally {
      state.authPendingAction = null;
      releaseLock("switch", lockToken);
      updateAuthChrome();
    }
  }

  function restoreWorkspaceSnapshot(snapshot) {
    if (!snapshot) return;
    state.eventSince = snapshot.eventSince;
    state.conversations = (snapshot.conversations || []).slice();
    state.convMeta = JSON.parse(JSON.stringify(snapshot.convMeta || {}));
    state.currentUid = snapshot.currentUid || "";
    state.messages = (snapshot.messages || []).slice();
    state.orders = snapshot.orders ? JSON.parse(JSON.stringify(snapshot.orders)) : null;
    state.ordersLoading = snapshot.ordersLoading;
    state.ordersError = snapshot.ordersError || "";
    state.contextLoading = snapshot.contextLoading;
    state.contextError = snapshot.contextError || "";
    state.listenOn = snapshot.listenOn;
    state.loggedIn = snapshot.loggedIn;
    state.loginPhase = snapshot.loginPhase;
    state.activeAccountId = snapshot.activeAccountId || "";
    state.accounts = (snapshot.accounts || []).slice();
    state.convLastSuccess = snapshot.convLastSuccess
      ? { ...snapshot.convLastSuccess, items: (snapshot.convLastSuccess.items || []).slice() }
      : null;
    renderConvList();
    renderMessages();
    renderOrders();
    updateAuthChrome();
  }

  async function addAccount() {
    try {
      await startQrLogin();
    } catch (e) {
      toast("无法开始扫码: " + (e.message || e));
    }
  }

  function qrImgSrc() {
    if (state.qrImgSrc) return state.qrImgSrc;
    return "/api/qr-login/image?t=" + Date.now();
  }

  function qrLoginSucceeded(j) {
    const ctx = {
      qrGeneration: state.qrGeneration,
      currentQrGeneration: state.qrGeneration,
      qrTargetAccountId: state.qrTargetAccountId,
      qrTaskId: state.qrTaskId,
      currentAccountId: state.activeAccountId,
    };
    const confirmed = UI().confirmQrLoginSuccess ? UI().confirmQrLoginSuccess(j, ctx) : { ok: false };
    return confirmed.ok === true;
  }

  async function logoutCurrentAccount() {
    if ((!state.loggedIn && !state.activeAccountId) || isLockBusy("logout")) return;
    if (!confirm("确定退出当前店铺吗？退出后该店铺需要重新扫码登录。")) return;

    const lockToken = tryAcquireLock("logout");
    if (lockToken == null) return;

    const snapshot = UI().createWorkspaceSnapshot ? UI().createWorkspaceSnapshot(state) : null;
    const logoutAid = state.activeAccountId || "";
    state.authPendingAction = "logging_out";
    bumpAuthGeneration();
    updateAuthChrome();
    _lastLoginRenderKey = "";
    renderLogin({
      logged_in: state.loggedIn,
      qr: { phase: "logging_out", running: true },
      shop_name: state.lastSession?.shop_name || "当前店铺",
      onboard: { running: true, phase: "logging_out" },
    });

    try {
      stopQrPoll();
      const j = await api("/api/accounts/logout", {
        method: "POST",
        body: JSON.stringify({ account_id: logoutAid }),
        trackProgress: true,
      });
      if (lockToken !== actionLocks.logout.token) return;
      const valid = UI().validateLogoutResult ? UI().validateLogoutResult(j) : { ok: j.ok !== false, error: j.error };
      if (!valid.ok) {
        if (snapshot) restoreWorkspaceSnapshot(snapshot);
        toast("退出失败: " + (valid.error || "未知错误"));
        await refreshLogin(false);
        return;
      }

      resetWorkspaceState();
      state.listenOn = false;
      state.activeAccountId = valid.activeAccountId || j.active_account_id || j.switched_to || "";
      state.loggedIn = Boolean(valid.loggedIn);
      state.loginPhase = state.loggedIn ? "logged_in" : "logged_out";
      _lastLoginRenderKey = "";
      await refreshLogin(false);
      if (!state.loggedIn) {
        renderLogin({ logged_in: false, qr: { phase: "logged_out" }, shop_name: "飞鸽客服" });
      } else {
        await refreshConversations(true, state.activeCategory || "recent", { heavy: false });
      }
      toast(state.loggedIn ? "已切换至其他已登录店铺" : "已退出，请扫码登录");
    } catch (e) {
      if (lockToken !== actionLocks.logout.token) return;
      if (snapshot) restoreWorkspaceSnapshot(snapshot);
      toast("退出失败: " + (e.message || e));
      await refreshLogin(false);
    } finally {
      state.authPendingAction = null;
      releaseLock("logout", lockToken);
      updateAuthChrome();
    }
  }

  async function completeQrLoginSuccess(j) {
    const ctx = {
      qrGeneration: state.qrGeneration,
      currentQrGeneration: state.qrGeneration,
      qrTargetAccountId: state.qrTargetAccountId,
      qrTaskId: state.qrTaskId,
      currentAccountId: state.activeAccountId,
    };
    const confirmed = UI().confirmQrLoginSuccess ? UI().confirmQrLoginSuccess(j, ctx) : { ok: false };
    if (!confirmed.ok) return;

    stopQrPoll();
    bumpAuthGeneration();
    const sendOk = confirmed.sendReady === true;
    toast(
      sendOk ? "登录成功，已进入客服工作台" : "登录成功，消息通道预热中，不影响查看会话"
    );
    _lastLoginRenderKey = "";
    state.loggedIn = true;
    state.loginPhase = "logged_in";
    state.authStatus = UI().AUTH_STATUS?.LOGGED_IN || "logged_in";
    await refreshLogin(false);
    await refreshConversations(true, "recent", { heavy: false });
    if (state.loggedIn && j.listen_ready !== false) {
      await startListening();
    }
  }

  function renderLogin(j) {
    const body = $("loginBody");
    if (!body) return;
    if (state.authStatus === (UI().AUTH_STATUS?.CHECKING || "checking") && !state.qrPollingActive && !state.authLastTrusted) {
      const viewKey = "checking";
      if (viewKey === _lastLoginRenderKey) return;
      _lastLoginRenderKey = viewKey;
      body.innerHTML = `
        <p class="status-line"><strong>正在检查登录状态</strong></p>
        <p class="muted">请稍候，正在连接本地服务…</p>
        <div class="btn-row">
          <button type="button" class="btn ghost" disabled>检查中…</button>
        </div>`;
      return;
    }
    if (state.qrPollingActive) {
      j = {
        ...j,
        logged_in: false,
        session_alive: false,
        qr: { ...(j.qr || {}), running: true },
      };
      const qp = j.qr?.phase || "waiting_scan";
      if (qp === "logged_out" || qp === "expired" || qp === "error") {
        const hold =
          state.loginPhase === "scanned" || state.loginPhase === "bootstrapping"
            ? state.loginPhase
            : "waiting_scan";
        j.qr = { ...j.qr, phase: hold, error: "" };
      }
    }
    const qr = j.qr || {};
    const onboard = j.onboard || {};

    if ((qr.phase === "expired" || (qr.phase === "error" && qr.error)) && !state.qrPollingActive) {
      const ageMs = Date.now() - (state.qrStartedAt || 0);
      if (ageMs < 15000) return;
      const errText = String(qr.error || "");
      const isExpiredMsg = qr.phase === "expired" || /过期|expired/i.test(errText);
      const viewKey = `expired:${qr.phase}:${errText}`;
      if (viewKey === _lastLoginRenderKey) return;
      _lastLoginRenderKey = viewKey;
      body.innerHTML = `
        <p class="status-line"><strong>${isExpiredMsg ? "二维码已过期" : "登录失败"}</strong></p>
        <p class="muted">${errText || "请点击下方按钮重新获取"}</p>
        <div class="btn-row">
          <button type="button" class="btn ghost" id="btnRefreshQr">刷新二维码</button>
        </div>`;
      return;
    }

    const viewKey = loginViewKey(j);
    if (viewKey === _lastLoginRenderKey) return;
    _lastLoginRenderKey = viewKey;

    const qrActive = isQrFlowActive(j);
    const onboardBusy = onboard.running && onboard.phase !== "idle" && onboard.phase !== "done";
    if (effectiveLoggedIn(j) && !qrActive && !onboardBusy) {
      const sendOk = j.send_ready !== false;
      const needsRenew = j.needs_renew || (j.session_alive && !j.backstage_ok);
      const blockers = j.blockers || onboard.blockers || qr.blockers || [];
      const action = j.recommended_action || onboard.recommended_action || "";
      const warn =
        !sendOk && blockers.length && !CS_MODE
          ? `<p class="status-err">${blockers.join("；")}</p>`
          : needsRenew && !sendOk && !CS_MODE
            ? `<p class="status-err">抖店已登录，飞鸽会话需续期（可点下方按钮，无需重新扫码）</p>`
            : "";
      let fixBtn = "";
      if (!CS_MODE) {
        if (needsRenew && !sendOk) {
          fixBtn = '<button type="button" class="btn ghost" id="btnRenewSession">手动续期</button>';
        } else if (action === "cdp_warm_inners" || action === "rust_sdk_inner") {
          fixBtn =
            action === "rust_sdk_inner"
              ? '<button type="button" class="btn primary" id="btnWarmInners">生成发信密钥（169B）</button>'
              : '<button type="button" class="btn primary" id="btnWarmInners">预热发信（169B）</button>';
        } else if (!sendOk) {
          fixBtn =
            '<button type="button" class="btn primary" id="btnStartQr">扫码登录</button>' +
            '<button type="button" class="btn ghost" id="btnReOnboard">浏览器登录</button>';
        }
      }
      body.innerHTML = `
        <div class="login-profile">
          <div class="avatar lg">${avatarChar(j.shop_name || "店")}</div>
          <div>
            <strong>${j.shop_name || "已登录店铺"}</strong>
            <p class="muted">${sendOk ? "在线 · 可收发消息" : "在线 · 消息同步中"}</p>
            ${warn}
          </div>
        </div>
        <div class="btn-row">
          <button type="button" class="btn ghost" id="btnReQrLogin">切换店铺 / 扫码登录</button>
          <button type="button" class="btn ghost warn sm" id="btnLogoutShop">退出当前店铺</button>
          ${fixBtn}
        </div>`;
      return;
    }

    const phase = onboard.running ? onboard.phase || "starting" : qr.phase || "logged_out";
    if ((onboard.running || state.authPendingAction === "logging_out") && ONBOARD_PHASES[phase]) {
      const [title, sub] = ONBOARD_PHASES[phase];
      const err = phase === "error" ? `<div class="status-err">${onboard.error || "请重试"}</div>` : "";
      body.innerHTML = `
        <p class="status-line"><strong>${title}</strong></p>
        <p class="muted">${sub}</p>
        ${err}
        <div class="btn-row">
          <button type="button" class="btn ghost" disabled>登录进行中…</button>
        </div>`;
      return;
    }

    const map = {
      logged_out: ["未登录", "点击下方按钮获取二维码，用抖音/抖店 App 扫码", "btnStartQr"],
      fetching: ["正在获取二维码", "请稍候…", null],
      waiting_scan: ["等待扫码", "请用抖音/抖店 App 扫码（约 60 秒自动换新码）", "btnRefreshQr"],
      scanned: ["已扫码", "请在手机上确认登录（确认后请稍候）", null],
      bootstrapping: ["正在完成登录", "正在写入会话…", null],
      expired: ["二维码已过期", "点击下方按钮刷新", "btnRefreshQr"],
      error: ["登录失败", qr.error || "请重试", "btnRefreshQr"],
      logged_in: ["登录成功", "正在同步会话…", null],
    };
    const [title, sub] = map[phase] || map.logged_out;
    let qrImg = "";
    const showPulse = false;
    const showQrImg = ["fetching", "waiting_scan", "scanned"].includes(phase);
    if (showQrImg) {
      qrImg = `<div class="qr-box"><img src="${qrImgSrc()}" alt="抖店登录二维码" onerror="this.onerror=null;this.src='/api/qr-login/image?t='+Date.now();"/></div>`;
    }
    body.innerHTML = `
      <p class="status-line"><strong>${title}</strong></p>
      <p class="muted">${sub}</p>
      ${showPulse ? '<div class="login-pulse-bar" aria-hidden="true"></div>' : ""}
      ${qrImg}
      ${phase === "error" ? `<div class="status-err">${qr.error || "登录出错"}</div>` : ""}
      <div class="btn-row">
        <button type="button" class="btn primary" id="btnStartQr">扫码登录</button>
        ${CS_MODE ? "" : '<button type="button" class="btn ghost" id="btnStartCdp">浏览器登录（备用）</button>'}
        ${phase === "expired" || phase === "error" || phase === "waiting_scan" || phase === "fetching" ? '<button type="button" class="btn ghost" id="btnRefreshQr">刷新二维码</button>' : ""}
      </div>`;
  }

  async function renewSession() {
    toast("正在续期飞鸽会话…");
    const j = await api("/api/session/renew", { method: "POST", body: "{}" });
    if (j.ok) {
      toast("飞鸽会话续期成功");
      await refreshProtocolStatus();
      await refreshLogin();
      return;
    }
    toast(j.error || j.needs_cdp_onboard ? "续期失败，请使用浏览器登录" : "续期未完成", 5000);
    await refreshProtocolStatus();
    await refreshLogin();
  }

  async function startCdpWarm() {
    toast("正在生成发信密钥（169B）…");
    const j = await api("/api/cdp-warm/start", { method: "POST", body: "{}" });
    if (j.error && !j.started) {
      toast("预热失败: " + j.error);
      return;
    }
    const tick = async () => {
      const st = await api("/api/cdp-warm/status", BG_API);
      const warm = st.warm || st;
      if (warm.running) {
        setTimeout(tick, 1500);
        return;
      }
      if (warm.phase === "done" || st.send_ready) {
        toast("发信预热完成");
        await refreshProtocolStatus();
        await refreshLogin();
        return;
      }
      toast(warm.error || "预热未完成，请确认已在浏览器登录飞鸽");
    };
    tick();
  }

  async function startCdpOnboard() {
    renderLogin({ logged_in: false, onboard: { running: true, phase: "starting" } });
    const j = await api("/api/cdp-onboard/start", { method: "POST", body: "{}" });
    if (j.error && !j.started) {
      toast("启动失败: " + j.error);
      renderLogin({ logged_in: false, onboard: { phase: "error", error: j.error } });
      return;
    }
    pollCdpOnboard();
  }

  async function pollCdpOnboard() {
    const tick = async () => {
      const j = await api("/api/cdp-onboard/status", BG_API);
      const ob = j.onboard || j;
      state.loggedIn = Boolean(j.logged_in);
      updateAuthChrome();
      renderLogin({
        logged_in: j.logged_in,
        send_ready: j.send_ready,
        listen_ready: j.listen_ready,
        blockers: j.blockers,
        onboard: ob,
        shop_name: j.shop_name,
      });
      if (ob.running) {
        setTimeout(tick, 1500);
        return;
      }
      if (ob.phase === "done" || (j.logged_in && j.send_ready)) {
        toast(j.send_ready ? "登录成功，发信已就绪" : "登录完成（发信未就绪）");
        await refreshProtocolStatus();
        await refreshLogin();
        await refreshConversations();
        if (j.listen_ready !== false) {
          await startListening();
        }
        return;
      }
      if (ob.phase === "error") {
        toast(ob.error || "浏览器登录失败");
        return;
      }
      setTimeout(tick, 1500);
    };
    tick();
  }

  let _qrImgTimer = null;

  function scheduleQrImgRefresh() {
    clearInterval(_qrImgTimer);
    _qrImgTimer = setInterval(() => {
      const img = document.querySelector(".qr-box img");
      if (!img) return;
      img.src = "/api/qr-login/image?t=" + Date.now();
    }, 2500);
  }

  function stopQrImgRefresh() {
    clearInterval(_qrImgTimer);
    _qrImgTimer = null;
  }

  async function waitBridgeReady(maxMs = 15000) {
    const start = Date.now();
    while (Date.now() - start < maxMs) {
      const h = await api("/api/health", { trackProgress: false, timeoutMs: 4000 });
      if (h.bridge_ready === true || (h.ok !== false && h.go_api_ok === true && !h.degraded)) {
        return true;
      }
      const s = await api("/api/session?light=1", { trackProgress: false, timeoutMs: 4000 });
      if (s && (s.accounts || s.logged_in !== undefined)) {
        if (h.ok !== false) return true;
      }
      setConn(true, "Bridge 初始化中…");
      await new Promise((r) => setTimeout(r, 600));
    }
    return false;
  }
  async function startQrLogin() {
    if (isLockBusy("qr") || isLockBusy("logout") || isLockBusy("switch")) return;
    const lockToken = tryAcquireLock("qr");
    if (lockToken == null) return;

    stopQrPoll();
    bumpAuthGeneration();
    state.qrGeneration += 1;
    _lastLoginRenderKey = "";
    state.loggedIn = false;
    state.qrImgSrc = "";
    state.qrPollingActive = true;
    state.qrStartedAt = Date.now();
    state.loginPhase = "fetching";
    state.qrTaskId = "";
    state.qrTargetAccountId = state.activeAccountId || "";
    state.authStatus = UI().AUTH_STATUS?.LOGGING_IN || "logging_in";
    updateAuthChrome();
    renderLogin({ logged_in: false, qr: { phase: "fetching", running: true } });

    try {
      await waitBridgeReady();
      if (lockToken !== actionLocks.qr.token) return;
      const j = await api("/api/qr-login/start", { method: "POST", body: "{}", trackProgress: true });
      if (lockToken !== actionLocks.qr.token) return;
      if (j.switched_from) {
        state.activeAccountId = j.account_id || state.activeAccountId;
        state.qrTargetAccountId = state.activeAccountId;
        toast(`已切换到空账号槽 ${j.account_id || ""}，请扫码`);
      }
      if (j.ok === false || j.qr?.phase === "error") {
        state.qrPollingActive = false;
        toast(j.qr?.error || j.error || "获取二维码失败");
        _lastLoginRenderKey = "";
        renderLogin({ logged_in: false, qr: j.qr || { phase: "error", error: j.error || "获取二维码失败" } });
        return;
      }
      state.qrTaskId = String(j.qr?.job_id || j.job_id || "");
      if (j.qrcode_b64) {
        state.qrImgSrc = "data:image/png;base64," + j.qrcode_b64;
      }
      scheduleQrImgRefresh();
      _lastLoginRenderKey = "";
      const phase = j.qr?.phase === "fetching" ? "fetching" : "waiting_scan";
      state.loginPhase = phase;
      renderLogin({
        logged_in: false,
        qr: { ...(j.qr || {}), phase, running: true, job_id: state.qrTaskId },
      });
      const img = document.querySelector(".qr-box img");
      if (img) img.src = qrImgSrc();
      if (phase === "fetching") {
        setTimeout(() => {
          if (state.qrPollingActive && lockToken === actionLocks.qr.token) pollQrStatus(state.qrGeneration, lockToken);
        }, 800);
      } else {
        pollQrStatus(state.qrGeneration, lockToken);
      }
    } finally {
      releaseLock("qr", lockToken);
    }
  }

  async function pollQrStatus(qrGen, qrLockToken) {
    const gen = ++_qrPollGen;
    const tick = async () => {
      if (gen !== _qrPollGen || qrGen !== state.qrGeneration) return;
      const j = await api("/api/qr-login/status", BG_API);
      if (gen !== _qrPollGen || qrGen !== state.qrGeneration) return;
      const qr = j.qr || {};
      if (qr.phase === "scanned") state.loginPhase = "scanned";
      if (qr.phase === "bootstrapping") state.loginPhase = "bootstrapping";
      if (qr.phase === "waiting_scan") state.loginPhase = "waiting_scan";

      if (j.ok === false && state.qrPollingActive && Date.now() - state.qrStartedAt < 10 * 60 * 1000) {
        setTimeout(tick, 1500);
        return;
      }
      if (qr.qr_refreshed_at && qr.qr_refreshed_at !== state.lastQrRefreshAt) {
        state.lastQrRefreshAt = qr.qr_refreshed_at;
        const img = document.querySelector(".qr-box img");
        if (img) img.src = "/api/qr-login/image?t=" + Date.now();
      }
      const bridgeGlitch =
        state.qrPollingActive &&
        !j.logged_in &&
        Date.now() - state.qrStartedAt < 10 * 60 * 1000 &&
        (qr.phase === "logged_out" || (!qr.running && qr.phase !== "expired" && qr.phase !== "error"));
      if (bridgeGlitch) {
        const holdPhase = ["scanned", "waiting_scan", "fetching", "bootstrapping"].includes(state.loginPhase)
          ? state.loginPhase
          : "waiting_scan";
        renderLogin({ logged_in: false, qr: { phase: holdPhase, running: true } });
        setTimeout(tick, 1500);
        return;
      }
      if (j.qr?.phase === "error" || j.qr?.phase === "logged_out") {
        if (
          state.qrPollingActive &&
          Date.now() - state.qrStartedAt < 10 * 60 * 1000
        ) {
          const errText = String(j.qr?.error || "");
          const afterConfirm = /过期|expired/i.test(errText) || state.loginPhase === "scanned" || state.loginPhase === "bootstrapping";
          renderLogin({
            logged_in: false,
            qr: { phase: afterConfirm ? "bootstrapping" : "waiting_scan", running: true, error: "" },
          });
          setTimeout(tick, afterConfirm ? 400 : 1500);
          return;
        }
        stopQrPoll();
        return;
      }
      if (j.qr?.phase === "expired") {
        if (state.qrPollingActive) {
          renderLogin({
            logged_in: false,
            qr: { phase: "bootstrapping", running: true, error: "" },
          });
          setTimeout(tick, 400);
          return;
        }
        stopQrPoll();
        renderLogin(j);
        return;
      }
      state.loggedIn = effectiveLoggedIn(j);
      syncLoginState(j, { trust: false });
      if (state.qrPollingActive && qrLoginSucceeded(j)) {
        await completeQrLoginSuccess(j);
        return;
      }
      renderLogin(j);
      if (j.qr?.phase === "bootstrapping") {
        if (qrLoginSucceeded(j)) {
          await completeQrLoginSuccess(j);
          return;
        }
        if (
          state.qrPollingActive &&
          Date.now() - state.qrStartedAt > 3 * 60 * 1000 &&
          !j.logged_in
        ) {
          stopQrPoll();
          renderLogin({
            logged_in: false,
            qr: {
              phase: "error",
              error: "登录超时，请刷新二维码重试",
            },
          });
          return;
        }
        setTimeout(tick, 1200);
        return;
      }
      if (j.qr?.running && j.qr?.phase === "fetching") {
        setTimeout(tick, 1200);
        return;
      }
      if (j.logged_in && qrLoginSucceeded(j) && !isQrFlowActive(j)) {
        stopQrPoll();
        await completeQrLoginSuccess(j);
        return;
      }
      if (!isQrFlowActive(j)) {
        if (state.qrPollingActive && Date.now() - state.qrStartedAt < 10 * 60 * 1000) {
          const holdPhase = ["scanned", "bootstrapping", "waiting_scan", "fetching"].includes(
            state.loginPhase
          )
            ? state.loginPhase
            : "waiting_scan";
          renderLogin({ logged_in: false, qr: { phase: holdPhase, running: true } });
          setTimeout(tick, 1500);
          return;
        }
        stopQrPoll();
        return;
      }
      const delay =
        j.qr?.phase === "scanned" || j.qr?.phase === "bootstrapping"
          ? 400
          : j.qr?.phase === "waiting_scan"
            ? 1000
            : 1500;
      setTimeout(tick, delay);
    };
    tick();
  }

  /* ——— 会话列表 ——— */
  function renderConvTabs() {
    const tabs = $("convTabs");
    tabs.innerHTML = CATEGORIES.map(
      (c) => `<button type="button" class="conv-tab${state.activeCategory === c.id ? " active" : ""}" data-cat="${c.id}">${c.label}</button>`
    ).join("");
    tabs.querySelectorAll(".conv-tab").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const cat = btn.dataset.cat;
        state.activeCategory = cat;
        renderConvTabs();
        if (FETCH_CONV_CATEGORIES.has(cat) && isActiveAccountLoggedIn()) {
          await refreshConversations(true, cat);
        } else if (FETCH_CONV_CATEGORIES.has(cat) && !isActiveAccountLoggedIn()) {
          toast("当前账号未登录，请先扫码或切换已登录店铺");
          renderConvList();
        } else {
          renderConvList();
        }
      });
    });
  }

  function filterConversations() {
    const items = state.conversations;
    const cat = state.activeCategory;
    if (cat === "all" || cat === "recent") return items;
    if (cat === "no_reply") {
      return items.filter((c) => {
        const m = state.convMeta[c.security_user_id];
        return m?.aiStatus === "wait" || m?.unread;
      });
    }
    if (cat === "ai_replied") {
      return items.filter((c) => state.convMeta[c.security_user_id]?.aiStatus === "done");
    }
    if (cat === "has_order") {
      return items.filter((c) => state.convMeta[c.security_user_id]?.hasOrder);
    }
    if (cat === "risk") {
      return items.filter((c) => state.riskWatch.has(c.security_user_id));
    }
    return items;
  }

  function renderConvList() {
    const list = $("convList");
    const items = filterConversations();
    $("convCount").textContent = `${items.length} 人`;
    if (!items.length) {
      list.innerHTML = `<div class="empty-mini">${convListEmptyHint(state.activeCategory)}</div>`;
      return;
    }
    list.innerHTML = items
      .map((c, i) => {
        const uid = c.security_user_id;
        const meta = state.convMeta[uid] || {};
        const unread = Math.max(
          Number(c.unread_count) || 0,
          meta.unread && uid !== state.currentUid ? 1 : 0
        );
        const timeStr = c.last_time || meta.lastTime || "";
        const tags = [];
        if (meta.aiStatus === "wait") tags.push('<span class="tag ai-wait">待回复</span>');
        if (meta.aiStatus === "gen") tags.push('<span class="tag ai-gen">生成中</span>');
        if (meta.aiStatus === "done") tags.push('<span class="tag ai-done">已回复</span>');
        if (state.markedHuman.has(uid)) tags.push('<span class="tag ai-human">需人工</span>');
        if (meta.hasOrder) tags.push('<span class="tag order">有订单</span>');
        return `
        <div class="conv-item${uid === state.currentUid ? " active" : ""}" data-uid="${uid}" style="animation-delay:${i * 0.03}s">
          <div class="conv-head">
            <div class="name">${convName(c)}</div>
            <div class="conv-side">
              ${timeStr ? `<span class="conv-time">${escapeHtml(timeStr)}</span>` : ""}
              ${unread > 0 ? `<span class="unread-badge">${unread > 99 ? "99+" : unread}</span>` : ""}
            </div>
          </div>
          <div class="preview">${escapeHtml(convPreview(c) || meta.lastPreview || "暂无消息")}</div>
          <div class="meta">${tags.join("")}</div>
        </div>`;
      })
      .join("");
    list.querySelectorAll(".conv-item").forEach((el) => {
      el.addEventListener("click", () => selectConversation(el.dataset.uid));
    });
  }

  async function enrichConvDisplayNames() {
    const accountGen = state.accountGeneration;
    const accountId = state.activeAccountId;
    const pending = (state.conversations || []).filter((c) => {
      const uid = String(c.security_user_id || "");
      if (!uid || isUidFallbackName(convName(c), uid) === false) return false;
      if (state.enrichInFlight.has(uid)) return false;
      const failedAt = Number(state.enrichFailedAt[uid] || 0);
      if (failedAt && Date.now() - failedAt < 5 * 60 * 1000) return false;
      return true;
    });
    if (!pending.length) return;
    for (const c of pending.slice(0, 5)) {
      if (accountGen !== state.accountGeneration || accountId !== state.activeAccountId) return;
      const uid = c.security_user_id;
      state.enrichInFlight.add(uid);
      try {
        const j = await api(`/api/context?user_id=${encodeURIComponent(uid)}`, {
          ...BG_API,
          timeoutMs: 5000,
        });
        if (accountGen !== state.accountGeneration || accountId !== state.activeAccountId) return;
        const ctx = j.context || {};
        const name = cleanBuyerName(ctx.buyer_name);
        if (!name || isUidFallbackName(name, uid)) {
          state.enrichFailedAt[uid] = Date.now();
          continue;
        }
        const meta = state.convMeta[uid] || {};
        meta.buyerName = name;
        state.convMeta[uid] = meta;
        c.display_name = name;
        c.buyer_name = name;
        c.name = name;
        const itemEl = document.querySelector(`.conv-item[data-uid="${CSS.escape(uid)}"] .name`);
        if (itemEl) itemEl.textContent = name;
      } catch {
        state.enrichFailedAt[uid] = Date.now();
      } finally {
        state.enrichInFlight.delete(uid);
      }
    }
    if (state.currentUid) updateBuyerMeta(state.currentUid);
  }

  function renderConvListRefreshingIndicator(show) {
    const countEl = $("convCount");
    if (!countEl) return;
    if (show) countEl.classList.add("is-refreshing");
    else countEl.classList.remove("is-refreshing");
  }

  async function refreshConversations(showProgress = false, category, { heavy = false } = {}) {
    const cat = category || state.activeCategory;
    if (!isActiveAccountLoggedIn()) {
      state.conversations = [];
      state.convListStatus = "idle";
      renderConvList();
      return;
    }

    const requestId = ++state.convListRequestId;
    const accountGen = state.accountGeneration;
    const accountId = state.activeAccountId;
    const userInitiated = Boolean(showProgress);
    const hasExisting = (state.conversations || []).length > 0 || Boolean(state.convLastSuccess?.items?.length);

    if (userInitiated || !hasExisting) {
      state.convListStatus = "loading";
      $("convList").innerHTML = `<div class="skeleton-stack pad"><div class="skeleton line"></div><div class="skeleton line w70"></div></div>`;
    } else {
      state.convListStatus = "refreshing";
      renderConvListRefreshingIndicator(true);
    }

    if (_convAbortController) _convAbortController.abort();
    _convAbortController = new AbortController();
    const signal = _convAbortController.signal;

    try {
      const qs = new URLSearchParams({ page: "0", size: "50" });
      const apiCategory = convCategoryParam(cat);
      if (apiCategory) qs.set("category", apiCategory);
      if (!heavy) qs.set("light", "1");
      const j = await api(`/api/conversations?${qs}`, {
        ...(userInitiated ? { trackProgress: true } : BG_API),
        timeoutMs: heavy ? 8000 : 5000,
        signal,
      });

      const resolved = UI().resolveConvRefreshResult
        ? UI().resolveConvRefreshResult(j, {
            requestId,
            latestRequestId: state.convListRequestId,
            accountGeneration: accountGen,
            snapshotAccountGeneration: state.accountGeneration,
            category: cat,
            snapshotCategory: state.activeCategory,
            userInitiated,
          })
        : { apply: requestId === state.convListRequestId, items: j.items || [], keepPrevious: j.timeout };

      if (!resolved.apply) {
        if (resolved.keepPrevious && state.convLastSuccess?.items?.length) {
          state.conversations = state.convLastSuccess.items.slice();
        }
        if (resolved.showDegraded && !userInitiated) {
          toast("会话列表后台刷新超时，已保留上次数据", 3000);
        } else if (resolved.showError) {
          toast((j.raw && j.raw.error) || j.error || "会话列表拉取失败，已保留上次数据");
        }
        state.convListStatus = state.conversations.length ? "ready" : "error";
        renderConvList();
        return;
      }

      state.conversations = resolved.items || [];
      state.convLastSuccess = {
        items: state.conversations.slice(),
        accountId,
        category: cat,
        at: Date.now(),
        accountGeneration: accountGen,
      };
      const via = (j.raw && j.raw.via) || j.via || "";
      if (state.conversations.length === 0 && j.ok !== false && resolved.explicitEmpty) {
        if (/fallback/.test(via)) {
          toast("工作台接口暂无会话，已从本地缓存加载联系人", 5000);
        } else if (userInitiated) {
          toast("暂无最近联系人（店铺当前没有待处理会话）", 4000);
        }
      }
    } catch (e) {
      if (requestId !== state.convListRequestId || accountGen !== state.accountGeneration) return;
      if (userInitiated) toast("无法拉取会话: " + (e.message || e));
      if (state.convLastSuccess?.items?.length && state.convLastSuccess.accountId === accountId) {
        state.conversations = state.convLastSuccess.items.slice();
      } else if (userInitiated) {
        state.conversations = [];
      }
      state.convListStatus = state.conversations.length ? "ready" : "error";
    } finally {
      if (requestId === state.convListRequestId) {
        renderConvListRefreshingIndicator(false);
      }
    }

    if (requestId !== state.convListRequestId || accountGen !== state.accountGeneration) return;

    state.conversations.forEach((c) => {
      const uid = c.security_user_id;
      if (!uid) return;
      if (!state.convMeta[uid]) state.convMeta[uid] = { aiStatus: "wait" };
    });
    state.convListStatus = "ready";
    renderConvList();
    void enrichConvDisplayNames();
    if (!state.currentUid && state.conversations[0]?.security_user_id) {
      await selectConversation(state.conversations[0].security_user_id);
    }
  }

  /* ——— 选中会话 ——— */
  function updateBuyerMeta(uid) {
    const conv = state.conversations.find((c) => c.security_user_id === uid);
    const meta = state.convMeta[uid] || {};
    const uidTail = uid ? uid.slice(-6) : "";
    const srcHint = conv?.buyer_source ? ` · 来源：${conv.buyer_source}` : "";
    if (state.contextLoading) {
      $("buyerMeta").textContent = uidTail
        ? `UID 尾号 ${uidTail}${srcHint} · 正在加载聊天记录…`
        : "正在加载聊天记录…";
      return;
    }
    $("buyerMeta").textContent = `最近活跃 · ${state.messages.length} 条消息${meta.hasOrder ? " · 有订单" : ""}${srcHint}`;
  }

  async function loadConversationContext(uid, selectSeq) {
    const loadGen = ++_contextLoadGen;
    const accountGen = state.accountGeneration;
    state.contextLoading = true;
    state.contextError = "";
    renderMessages();

    const ctxRes = await api("/api/context?user_id=" + encodeURIComponent(uid), {
      ...BG_API,
      timeoutMs: 6000,
    });

    if (selectSeq !== _selectReqSeq || uid !== state.currentUid || loadGen !== _contextLoadGen) {
      return;
    }
    if (!UI().shouldApplyConversationData({
      selectSeq,
      currentSelectSeq: _selectReqSeq,
      uid,
      currentUid: state.currentUid,
      loadGen,
      currentLoadGen: _contextLoadGen,
      accountGeneration: accountGen,
      currentAccountGeneration: state.accountGeneration,
    })) {
      return;
    }

    state.contextLoading = false;
    const ctx = ctxRes.context && typeof ctxRes.context === "object" ? ctxRes.context : {};
    const msgs = Array.isArray(ctx.messages) ? ctx.messages : [];

    if (ctxRes.timeout) {
      state.messages = [];
      state.contextError = "聊天记录加载超时，可点击重试";
    } else if (ctxRes.error && !msgs.length && ctxRes.ok === false) {
      state.messages = [];
      state.contextError = ctxRes.error || "聊天记录加载失败，可点击重试";
    } else {
      state.messages = msgs;
      state.contextError = "";
      const conv = state.conversations.find((c) => c.security_user_id === uid);
      const fallbackName = conv ? convName(conv) : "买家";
      const meta = state.convMeta[uid] || {};
      meta.buyerName = cleanBuyerName(ctx.buyer_name) || fallbackName;
      state.convMeta[uid] = meta;
      if (conv && meta.buyerName && !isUidFallbackName(meta.buyerName, uid)) {
        conv.display_name = meta.buyerName;
        conv.buyer_name = meta.buyerName;
        conv.name = meta.buyerName;
      }
      renderConvList();
      updateBuyerMeta(uid);
    }
    renderMessages();
    renderBuyerOverview();
    renderInsight();

    if (!state.contextError && state.aiMode === "auto" && !state.humanTakeover && !state.markedHuman.has(uid)) {
      const last = [...state.messages].reverse().find((m) => isBuyerRole(m.role));
      if (last) void generateAiReply(last.text);
    }
  }

  async function loadConversationOrders(uid, selectSeq, { heavy = false } = {}) {
    const loadGen = ++_ordersLoadGen;
    const accountGen = state.accountGeneration;
    state.ordersLoading = true;
    state.ordersError = "";
    renderOrders();

    const qs = new URLSearchParams({ user_id: uid });
    if (heavy) qs.set("heavy", "1");
    else qs.set("fast", "1");

    const ordRes = await api("/api/orders?" + qs.toString(), {
      ...BG_API,
      timeoutMs: heavy ? 10000 : 4000,
    });

    if (selectSeq !== _selectReqSeq || uid !== state.currentUid || loadGen !== _ordersLoadGen) {
      return;
    }
    if (!UI().shouldApplyConversationData({
      selectSeq,
      currentSelectSeq: _selectReqSeq,
      uid,
      currentUid: state.currentUid,
      loadGen,
      currentLoadGen: _ordersLoadGen,
      accountGeneration: accountGen,
      currentAccountGeneration: state.accountGeneration,
    })) {
      return;
    }

    state.ordersLoading = false;
    state.orders = ordRes.orders && typeof ordRes.orders === "object" ? ordRes.orders : null;
    const cards = orderCards(state.orders);
    const hasOrdData = Boolean(state.orders?.has_order || cards.length);

    if (ordRes.timeout) {
      state.orders = null;
      state.ordersError = "订单加载超时，可点击右上角重试";
    } else if (ordRes.error && !hasOrdData) {
      state.ordersError = ordRes.error;
      if (!state.orders) {
        state.orders = { has_order: false, cards: [], summary: ordRes.error };
      }
    } else if (!hasOrdData && ordRes.order_ok === false && ordRes.error) {
      state.ordersError = ordRes.error;
    } else {
      state.ordersError = "";
    }

    const meta = state.convMeta[uid] || {};
    meta.hasOrder = hasOrdData;
    state.convMeta[uid] = meta;

    renderOrders();
    renderBuyerOverview();
    renderInsight();
    updateBuyerMeta(uid);
  }

  function retryCurrentContext() {
    const uid = state.currentUid;
    if (!uid) return Promise.resolve();
    return loadConversationContext(uid, _selectReqSeq);
  }

  function retryCurrentOrders() {
    const uid = state.currentUid;
    if (!uid) return Promise.resolve();
    return loadConversationOrders(uid, _selectReqSeq, { heavy: true });
  }

  async function selectConversation(uid) {
    if (!uid) return;
    const reqId = ++_selectReqSeq;
    state.currentUid = uid;
    const conv = state.conversations.find((c) => c.security_user_id === uid);
    const name = conv ? convName(conv) : "买家";
    $("buyerTitle").textContent = name;
    $("buyerAvatar").textContent = avatarChar(name);
    if (state.convMeta[uid]) {
      state.convMeta[uid].unread = false;
    }
    void api("/api/conversations/ack", { method: "POST", body: JSON.stringify({ user_id: uid }), ...BG_API });
    renderConvList();

    state.messages = [];
    state.orders = null;
    state.contextLoading = true;
    state.ordersLoading = true;
    state.contextError = "";
    state.ordersError = "";
    updateBuyerMeta(uid);
    renderMessages();
    renderOrders();

    void loadConversationContext(uid, reqId);
    void loadConversationOrders(uid, reqId);
  }

  function renderMessagesSkeleton() {
    $("msgEmpty").hidden = true;
    $("messageList").hidden = false;
    $("messageList").innerHTML = `<div class="skeleton-stack"><div class="skeleton line w80"></div><div class="skeleton line"></div><div class="skeleton line w70"></div></div>`;
  }

  function renderMessages() {
    const list = $("messageList");
    const empty = $("msgEmpty");

    if (state.contextLoading) {
      renderMessagesSkeleton();
      return;
    }

    if (state.contextError) {
      empty.hidden = true;
      list.hidden = false;
      list.innerHTML = `
        <div class="context-error">
          <strong>聊天记录加载失败</strong><br/>
          ${escapeHtml(state.contextError)}
          <div class="retry-row"><button type="button" class="btn ghost sm" data-action="retry-context">重试聊天记录</button></div>
        </div>`;
      const btn = list.querySelector('[data-action="retry-context"]');
      if (btn) btn.addEventListener("click", () => void retryCurrentContext());
      return;
    }

    if (!state.messages.length) {
      list.hidden = true;
      empty.hidden = false;
      empty.querySelector("p").textContent = "暂无历史消息";
      const hint = empty.querySelector(".muted");
      if (hint) hint.textContent = "有新消息后会显示在这里";
      return;
    }
    empty.hidden = true;
    list.hidden = false;
    let html = "";
    state.messages.forEach((m, i) => {
      const buyer = isBuyerRole(m.role);
      const rowClass = buyer ? "buyer" : "service";
      if (i > 0 && i % 6 === 0) {
        html += `<div class="msg-time">— 更早的消息 —</div>`;
      }
      html += `<div class="msg-row ${rowClass}"><div class="msg-bubble">${escapeHtml(m.text || "")}</div></div>`;
    });
    list.innerHTML = html;
    list.scrollTop = list.scrollHeight;
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /* ——— 订单 ——— */
  function renderOrderSkeleton() {
    const sk = `
      <div class="order-loading">
        <div class="order-loading-bar"><span></span></div>
        <div class="skeleton-stack">
          <div class="skeleton line w80"></div>
          <div class="skeleton block h80"></div>
        </div>
      </div>`;
    $("orderBody").innerHTML = sk;
    $("drawerOrderBody").innerHTML = sk;
  }

  function parseOrderTiles(orders) {
    if (!orders) return [];
    const raw = orders.orders || [];
    if (!raw.length && orders.raw?.data) {
      const cd = orders.raw.data.componentized_data;
      if (cd?.data) {
        Object.values(cd.data).forEach((v) => {
          if (v && typeof v === "object") raw.push(v);
        });
      }
    }
    return raw.slice(0, 3);
  }

  function orderCards(o) {
    if (!o) return [];
    if (Array.isArray(o.cards) && o.cards.length) return o.cards.slice(0, 5);
    return parseOrderTiles(o).map((t) => ({
      product_name: t.product_name || t.goods_name || t.title || "订单商品",
      amount: t.pay_amount || t.order_amount || t.price || "—",
      status: t.order_status_text || t.status_text || o.summary || "处理中",
      logistics: "",
      pay_time: "",
      ship_time: "",
      after_sale: "",
    }));
  }

  function renderOrderCard(card) {
    const title = card.product_name || "订单商品";
    const amount = card.amount || "—";
    const status = card.status || "处理中";
    const logistics = card.logistics || "";
    const payTime = card.pay_time || "";
    const shipTime = card.ship_time || "";
    const afterSale = card.after_sale || "";
    const products = Array.isArray(card.products) ? card.products : [];
    const extraProducts =
      products.length > 1
        ? `<div class="order-more muted">共 ${products.length} 件商品</div>`
        : "";
    return `
        <div class="order-tile">
          <div class="title">${escapeHtml(String(title))}</div>
          ${extraProducts}
          <div class="row"><span class="muted">金额</span><strong>${escapeHtml(String(amount))}</strong></div>
          <div class="row"><span class="muted">状态</span><span class="order-status">${escapeHtml(String(status))}</span></div>
          ${payTime ? `<div class="row"><span class="muted">付款</span><span>${escapeHtml(payTime)}</span></div>` : ""}
          ${shipTime ? `<div class="row"><span class="muted">发货</span><span>${escapeHtml(shipTime)}</span></div>` : ""}
          ${logistics ? `<div class="order-logistics">${escapeHtml(logistics)}</div>` : ""}
          ${afterSale ? `<div class="order-aftersale">售后：${escapeHtml(afterSale)}</div>` : ""}
        </div>`;
  }

  function renderOrders() {
    const body = $("orderBody");
    const drawer = $("drawerOrderBody");
    const o = state.orders;
    if (state.ordersLoading) {
      renderOrderSkeleton();
      return;
    }
    if (state.ordersError) {
      const html = `<div class="order-error">
          <strong>订单加载失败</strong><br/>${escapeHtml(state.ordersError)}
          <div class="retry-hint">点击右上角「重试」重新加载订单</div>
        </div>`;
      body.innerHTML = html;
      drawer.innerHTML = html;
      return;
    }
    const cards = orderCards(o);
    if (!o || (!o.has_order && !cards.length)) {
      const html = `<div class="empty-mini">该买家暂无订单<br/><span class="muted">有新订单后会显示在这里</span></div>`;
      body.innerHTML = html;
      drawer.innerHTML = html;
      return;
    }
    const html = cards.map(renderOrderCard).join("") || `<div class="order-tile"><div class="title">${escapeHtml(o.summary || "有订单")}</div></div>`;
    body.innerHTML = html;
    drawer.innerHTML = html;
  }

  function renderBuyerOverview() {
    const uid = state.currentUid;
    const meta = state.convMeta[uid] || {};
    const o = state.orders;
    const hasOrder = o?.has_order;
    $("buyerOverviewBody").innerHTML = `
      <div class="stat-grid">
        <div class="stat"><div class="label">订单数</div><div class="value">${hasOrder ? orderCards(o).length || 1 : 0}</div></div>
        <div class="stat"><div class="label">消息数</div><div class="value">${state.messages.length}</div></div>
        <div class="stat"><div class="label">AI 状态</div><div class="value" style="font-size:14px">${aiStatusLabel()}</div></div>
        <div class="stat"><div class="label">风险</div><div class="value" style="font-size:14px">${state.riskWatch.has(uid) ? "售后关注" : "正常"}</div></div>
      </div>`;
  }

  function aiStatusLabel() {
    const map = { idle: "空闲", analyzing: "分析中", generating: "生成中", ready: "已生成", sending: "发送中", sent: "已发送", fail: "失败" };
    return map[state.aiState] || "空闲";
  }

  function renderInsight() {
    const intent = INTENT_LABELS[state.aiIntent] || state.aiIntent || "待识别";
    $("insightBody").innerHTML = `
      <div class="insight-item"><strong>买家意图：</strong>${escapeHtml(intent)}</div>
      <div class="insight-item"><strong>回复方向：</strong>${state.aiDraft ? "已生成推荐回复，请确认后发送" : "结合聊天记录与订单信息整理回复"}</div>
      <div class="insight-item"><strong>注意：</strong>${state.riskWatch.has(state.currentUid) ? "售后关注中，建议多确认细节并保留关键信息" : "正常沟通，注意核实订单与材质信息"}</div>`;
  }

  /* ——— AI ——— */
  function setAiState(s, hint) {
    state.aiState = s;
    $("aiStateText").textContent = aiStatusLabel();
    $("aiHint").textContent = hint || $("aiHint").textContent;
    const pulsing = ["analyzing", "generating", "sending"].includes(s);
    $("aiPulse").hidden = !pulsing;
    $("aiPulseBar").hidden = !pulsing;
    $("aiDraft").hidden = s !== "ready" && s !== "sent" || !state.aiDraft;
    if (state.aiDraft) $("aiDraftText").textContent = state.aiDraft;
    const uid = state.currentUid;
    if (uid && state.convMeta[uid]) {
      if (s === "generating") state.convMeta[uid].aiStatus = "gen";
      else if (s === "ready" || s === "sent") state.convMeta[uid].aiStatus = "done";
      else if (s === "idle") state.convMeta[uid].aiStatus = "wait";
      renderConvList();
    }
    renderBuyerOverview();
    renderInsight();
  }

  async function generateAiReply(triggerText) {
    if (!state.currentUid || state.aiMode === "pause" || state.humanTakeover) return;
    setAiState("analyzing", "AI 正在看聊天记录和订单信息…");
    const recent = state.messages.slice(-12).map((m) => ({
      role: isBuyerRole(m.role) ? "customer" : "service",
      text: m.text || "",
    }));
    const lastQ = triggerText || [...recent].reverse().find((m) => m.role === "customer")?.text || "";
    try {
      setAiState("generating", "AI 正在整理更合适的回复…");
      const j = await api("/api/ai/suggest", {
        method: "POST",
        body: JSON.stringify({
          user_id: state.currentUid,
          message: lastQ,
          current_customer_question: lastQ,
          recent_messages: recent,
          buyer_name: state.convMeta[state.currentUid]?.buyerName,
          mode: "fast",
        }),
      });
      if (!j.ok || !j.reply) {
        setAiState("fail", j.message || "AI 暂时没整理好回复，可以稍后再试");
        return;
      }
      state.aiDraft = j.reply;
      state.aiIntent = j.intent || "other";
      $("composerInput").value = j.reply;
      setAiState("ready", "回复已整理好，确认后可以发送");
      if (state.aiMode === "auto" && !state.humanTakeover) {
        await sendMessage(j.reply, true);
      }
    } catch (e) {
      setAiState("fail", "AI 服务连接失败，请确认本地 RAG 已启动");
    }
  }

  async function sendMessage(text, fromAi = false) {
    const t = (text || $("composerInput").value).trim();
    if (!t || !state.currentUid) return;
    const btn = $("btnSend");
    btn.classList.add("loading");
    btn.disabled = true;
    if (fromAi) setAiState("sending", "正在发送回复…");
    try {
      const j = await api("/api/send", {
        method: "POST",
        body: JSON.stringify({ user_id: state.currentUid, text: t }),
      });
      if (j.ok) {
        toast(fromAi ? "AI 回复已发送" : "发送成功");
        $("composerInput").value = "";
        setAiState(fromAi ? "sent" : "idle", fromAi ? "本条 AI 回复已发出" : "");
        await selectConversation(state.currentUid);
      } else if (j.needs_cdp_onboard || j.recommended_action === "cdp_onboard") {
        toast((j.reason || j.blockers?.[0] || "发信未就绪") + " — 请使用浏览器登录", 6000);
        setAiState("fail", "需浏览器登录预热发信");
      } else if (j.recommended_action === "cdp_warm_inners") {
        toast((j.reason || "发信未就绪") + " — 可点击预热发信", 6000);
        setAiState("fail", "需预热 169B 发信密钥");
      } else if (j.preflight_failed) {
        toast(
          CS_MODE
            ? "当前店铺发信通道还在准备中，请稍后再试或点击高级修复"
            : j.reason || j.blockers?.[0] || "发信未就绪",
          5000
        );
        setAiState("fail", CS_MODE ? "发信通道准备中" : "发信未就绪");
      } else {
        toast("发送失败: " + (j.reason || "请重试"));
        setAiState("fail", "发送没成功，点发送再试一次");
      }
    } finally {
      btn.classList.remove("loading");
      btn.disabled = false;
    }
  }

  /* ——— 监听 ——— */
  let _eventFailCount = 0;

  async function pollEvents() {
    if (!state.listenOn) return;
    try {
      const q = `/api/events?since=${state.eventSince}&account_id=${encodeURIComponent(state.activeAccountId || "")}`;
      const j = await api(q, BG_API);
      if (j.ok === false) {
        _eventFailCount += 1;
        if (_eventFailCount >= 3) {
          setConn(false, "消息监听异常");
          await syncListenStatus();
          if (state.loggedIn && !state.listenOn) {
            await startListening();
            _eventFailCount = 0;
          }
        }
        return;
      }
      _eventFailCount = 0;
      (j.items || []).forEach((e) => {
        const eventAccount = e.account_id || "";
        if (eventAccount && state.activeAccountId && eventAccount !== state.activeAccountId) {
          return;
        }
        state.eventSince = e.seq;
        if (e.kind === "message" && e.message) {
          const m = e.message;
          const uid = m.security_user_id || state.currentUid;
          if (uid) {
            state.convMeta[uid] = state.convMeta[uid] || {};
            state.convMeta[uid].lastPreview = m.text;
            if (uid !== state.currentUid) {
              state.convMeta[uid].unread = true;
              const conv = state.conversations.find((c) => c.security_user_id === uid);
              if (conv) conv.unread_count = (Number(conv.unread_count) || 0) + 1;
            }
            state.convMeta[uid].aiStatus = "wait";
          }
          if (uid === state.currentUid) {
            void selectConversation(uid);
            if (isBuyerRole(m.role) && state.aiMode === "auto" && !state.humanTakeover) {
              void generateAiReply(m.text);
            }
          } else {
            renderConvList();
          }
        }
      });
    } catch {
      /* ignore */
    }
  }

  /* ——— 事件绑定 ——— */
  function useOrderDrawer() {
    return window.innerWidth <= 1100;
  }

  function setOrdersPanelOpen(open, { source = "auto" } = {}) {
    const wide = window.innerWidth > 1100;
    if (wide) {
      if (source === "user") state.desktopOrdersPrefOpen = open;
      state.ordersDrawerOpen = false;
    } else {
      state.ordersDrawerOpen = open;
    }
    applyOrdersPanelLayout();
  }

  function applyOrdersPanelLayout() {
    const wide = window.innerWidth > 1100;
    const panelOpen = wide ? state.desktopOrdersPrefOpen : state.ordersDrawerOpen;
    const ws = document.querySelector(".workspace");
    const drawer = $("orderDrawer");
    const backdrop = $("drawerBackdrop");
    if (wide) {
      ws?.classList.toggle("orders-collapsed", !panelOpen);
      drawer?.classList.remove("is-open");
      backdrop?.classList.remove("is-open");
      if (drawer) drawer.hidden = true;
      if (backdrop) backdrop.hidden = true;
    } else {
      ws?.classList.remove("orders-collapsed");
      drawer?.classList.toggle("is-open", panelOpen);
      backdrop?.classList.toggle("is-open", panelOpen);
      if (drawer) drawer.hidden = !panelOpen;
      if (backdrop) backdrop.hidden = !panelOpen;
    }
  }

  function syncOrdersPanelLayout() {
    const wide = window.innerWidth > 1100;
    const layout = UI().syncOrdersLayout
      ? UI().syncOrdersLayout({
          wide,
          prevWide: _ordersWideLayout,
          desktopPrefOpen: state.desktopOrdersPrefOpen,
          drawerOpen: state.ordersDrawerOpen,
        })
      : { desktopPrefOpen: state.desktopOrdersPrefOpen, drawerOpen: false, panelOpen: wide ? state.desktopOrdersPrefOpen : false, wide };
    state.desktopOrdersPrefOpen = layout.desktopPrefOpen;
    state.ordersDrawerOpen = layout.drawerOpen;
    _ordersWideLayout = layout.wide;
    applyOrdersPanelLayout();
  }

  function closeOrdersPanel() {
    setOrdersPanelOpen(false, { source: useOrderDrawer() ? "user" : "user" });
  }

  function toggleOrdersPanel() {
    if (!state.loggedIn) {
      toast("请先登录店铺后再查看订单");
      return;
    }
    const wide = window.innerWidth > 1100;
    const next = wide ? !state.desktopOrdersPrefOpen : !state.ordersDrawerOpen;
    setOrdersPanelOpen(next, { source: "user" });
  }

  function bindEvents() {
    $("loginBody")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn || !btn.id) return;
      const action = UI().loginBodyDelegatedAction
        ? UI().loginBodyDelegatedAction(btn.id)
        : null;
      if (!action) return;
      e.preventDefault();
      if (action === "start_qr" || action === "refresh_qr") {
        if (isLockBusy("qr")) return;
        void startQrLogin();
      } else if (action === "start_cdp") {
        void startCdpOnboard();
      } else if (action === "renew_session") {
        void renewSession();
      } else if (action === "warm_inners") {
        void startCdpWarm();
      } else if (action === "logout") {
        void logoutCurrentAccount();
      }
    });

    $("btnRefreshAll").addEventListener("click", () =>
      withBtnLoading($("btnRefreshAll"), async () => {
        await refreshLogin(false);
        if (state.loggedIn) {
          await refreshConversations(true, state.activeCategory || "recent", { heavy: false });
          if (state.currentUid) await selectConversation(state.currentUid);
        }
        toast("已刷新");
      })
    );
    $("btnLogoutAccount")?.addEventListener("click", () => void logoutCurrentAccount());
    $("accountSelect")?.addEventListener("change", (e) => {
      const id = e.target.value;
      if (isLockBusy("logout") || isLockBusy("switch")) {
        e.target.value = state.activeAccountId || "";
        return;
      }
      if (!id) {
        renderAccountPicker();
        void startQrLogin();
        return;
      }
      if (id === state.activeAccountId) return;
      switchAccount(id);
    });
    $("btnAddAccount")?.addEventListener("click", () => addAccount());
    $("btnToggleOrders").addEventListener("click", () => toggleOrdersPanel());
    $("btnCloseDrawer")?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeOrdersPanel();
    });
    $("drawerBackdrop")?.addEventListener("click", () => closeOrdersPanel());

    $("aiMode").addEventListener("change", (e) => {
      state.aiMode = e.target.value;
      const labels = { auto: "自动回复", confirm: "人工确认后发送", pause: "暂停 AI" };
      $("composerMode").textContent = "当前：" + labels[state.aiMode];
    });
    $("humanTakeover").addEventListener("change", (e) => {
      state.humanTakeover = e.target.checked;
      if (state.humanTakeover) setAiState("idle", "已切换为人工接管，AI 不会自动发送");
    });

    $("btnSend").addEventListener("click", () => sendMessage());
    $("composerInput")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
    document.addEventListener("keydown", (e) => {
      const wide = window.innerWidth > 1100;
      const drawerOpen = !wide && state.ordersDrawerOpen;
      if (e.key === "Escape" && drawerOpen) {
        closeOrdersPanel();
      }
    });
    $("btnAiGen").addEventListener("click", () => withBtnLoading($("btnAiGen"), () => generateAiReply()));
    $("btnSendAi").addEventListener("click", () => sendMessage(state.aiDraft, true));
    $("btnRegenAi").addEventListener("click", () => generateAiReply());
    $("btnCopyAi").addEventListener("click", () => {
      navigator.clipboard.writeText(state.aiDraft || "");
      toast("已复制推荐回复");
    });
    $("btnHumanAi").addEventListener("click", () => {
      state.markedHuman.add(state.currentUid);
      state.humanTakeover = true;
      $("humanTakeover").checked = true;
      renderConvList();
      toast("已标记为需人工处理");
    });
    $("btnRetryOrders").addEventListener("click", () => {
      if (!state.currentUid) return;
      withBtnLoading($("btnRetryOrders"), () => retryCurrentOrders());
    });
    $("btnQuickPhrase").addEventListener("click", () => {
      $("composerInput").value = "亲，您把看中的那款发我，我帮您看下细节和证书，咱不盲拍～";
    });
    $("actCopyBuyer").addEventListener("click", () => {
      const uid = state.currentUid || "";
      const name = state.convMeta[uid]?.buyerName || "";
      const text = name ? `${name}\n${uid}` : uid;
      navigator.clipboard.writeText(text);
      toast("已复制买家信息");
    });
    $("actCopyOrder").addEventListener("click", () => {
      navigator.clipboard.writeText(JSON.stringify(state.orders || {}, null, 2));
      toast("已复制订单信息");
    });
    $("actMarkHuman").addEventListener("click", () => $("btnHumanAi").click());
    $("actRisk").addEventListener("click", () => {
      state.riskWatch.add(state.currentUid);
      renderConvList();
      renderBuyerOverview();
      toast("已加入售后关注");
    });
  }

  async function init() {
    try {
      bindEvents();
      _progressCount = 0;
      $("globalProgress")?.classList.remove("done");
      if ($("globalProgress")) $("globalProgress").hidden = true;
      _ordersWideLayout = window.innerWidth > 1100;
      state.desktopOrdersPrefOpen = _ordersWideLayout;
      syncOrdersPanelLayout();
      window.addEventListener("resize", syncOrdersPanelLayout);
      state.authStatus = UI().AUTH_STATUS?.CHECKING || "checking";
      updateAuthChrome();
      renderConvTabs();
      setConn(true, "连接中…");
      await waitBridgeReady();
      const h = await api("/api/health", { ...BG_API, timeoutMs: 5000 });
      if (h.ok === false && !h.go_api_ok) setConn(false, h.error || "后端异常");
      else if (h.degraded || h.bridge_ready === false) setConn(true, "Bridge 初始化中");
      else setConn(true, "连接正常");
      void api("/api/session/bootstrap", { method: "POST", body: "{}", ...BG_API, timeoutMs: 3000 });
      void api("/api/session/keepalive", { method: "POST", body: "{}", ...BG_API, timeoutMs: 5000 });
      void refreshLogin();
      void refreshProtocolStatus();
      setInterval(pollEvents, 2000);
      setInterval(() => {
        if (state.loggedIn && !state.qrPollingActive) {
          refreshConversations(false, state.activeCategory, { heavy: false });
        }
      }, 90000);
      setInterval(async () => {
        try {
          const j = await api("/api/session/keepalive", { method: "POST", body: "{}", ...BG_API });
          if (j.renew?.ok || j.readiness?.backstage_ok) {
            await refreshProtocolStatus();
            if (j.readiness?.send_ready) await refreshLogin();
          }
        } catch {
          /* silent auto keepalive */
        }
      }, 10 * 60 * 1000);
    } catch (e) {
      console.error("[init]", e);
      setConn(false, "前端加载失败");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
