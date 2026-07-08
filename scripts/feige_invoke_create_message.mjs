#!/usr/bin/env node
/**
 * Direct Node probe: @pigeon-sdk/rust-sdk initSdk + invokeAsync(PigeonIMInit/PigeonIMCreateMessage).
 * Reads session JSON from FEIGE_SESSION_JSON env (default ../session/session.json).
 */
import { createRequire, Module } from "node:module";
import crypto from "node:crypto";
import {
  readFileSync,
  existsSync,
  mkdirSync,
  copyFileSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");

const SDK_ROOT = join(ROOT, "analysis/feige_electron_sdk");
const API_JS = process.env.PIGEON_RUST_SDK_API || join(SDK_ROOT, "rust-sdk-api/index.js");
const SESSION_PATH = process.env.FEIGE_SESSION_JSON || join(ROOT, "session/session.json");
const NODE_MODULES = process.env.PIGEON_RUST_SDK_NODE_MODULES || join(SDK_ROOT, "node_modules");

const CMD_INIT = 11300;
const CMD_WS_STATE = 11305;
const CMD_WS_WAIT = 11306;
const CMD_CREATE = 11327;
const CMD_CREATE_USER = 11200;
const CMD_GET_CONV = 11333;
const CMD_GET_CONV_LIST = 11334;
const CMD_FETCH_CONV = 11308;
const CMD_REFRESH_CONV = 11323;
const CMD_PULL_LATEST = 11355;
const CMD_SEND_BIZ = 11301;
const CMD_SEND_WITH_CREATE = 11319;
const CMD_CLOUD_SEND = 11320;
const CMD_SET_FLIGHT = 11331;
const CMD_SET_STRATEGY = 11359;
const CMD_GET_MESSAGE = 11328;
const CMD_UPDATE_NET = 11363;
const CMD_MESSAGE_SEND_PUSH = 11345;
const API_CRYPTO_KEY = "s5v8y/B?E(G+KbPe";

const FEIGE_WS = {
  host: "wss://ws.fxg.jinritemai.com/ws/v2",
  altHost: "wss://ws.jinritemai.com/ws/v2",
  aid: "1383",
  fpid: "92",
  access_key: "edc810b287161555b85f088064f8ead1",
  alt_access_key: "b42d99769353ce6304e74fb597e36e90",
  version_code: "10000",
};

const rustSdkDir = join(SDK_ROOT, "rust-sdk");
const nativePkg = process.env.PIGEON_RUST_SDK_NATIVE || join(SDK_ROOT, "rust-sdk-win32-x64-msvc");

function decryptHexStr(hexStr) {
  const key = Buffer.from(API_CRYPTO_KEY, "utf8");
  const data = Buffer.from(hexStr, "hex");
  const decipher = crypto.createDecipheriv("aes-128-ecb", key, null);
  decipher.setAutoPadding(true);
  const plain = Buffer.concat([decipher.update(data), decipher.final()]).toString("utf8");
  return plain.replace(/[\u0001-\u0010]/gu, "").trim();
}

function loadSession() {
  const raw = JSON.parse(readFileSync(SESSION_PATH, "utf8"));
  const cookies = raw.cookies || {};
  const qt = raw.query_tokens || {};
  const wsUrls = raw.ws_urls || [];

  // query_tokens.token is authoritative (HTTP bootstrap); ws_urls may be stale history
  let wsToken = String(qt.token || "");
  if (!wsToken) {
    for (const u of wsUrls) {
      const m = String(u).match(/[?&]token=([^&]+)/);
      if (m) {
        wsToken = decodeURIComponent(m[1]);
        break;
      }
    }
  }

  let pigeonSign = qt.pigeon_sign || "";
  if (wsToken && wsUrls.length) {
    for (const u of wsUrls) {
      const ustr = String(u);
      if (!ustr.includes(`token=${encodeURIComponent(wsToken)}`) && !ustr.includes(`token=${wsToken}`)) {
        continue;
      }
      const sm = ustr.match(/[?&]pigeon_sign=([^&]+)/);
      if (sm) {
        pigeonSign = decodeURIComponent(sm[1]);
        break;
      }
    }
  }

  const deviceId = String(raw.device_id || cookies.PIGEON_CID || qt.device_id || "");

  function synthesizeWsUrl(token, sign) {
    if (!token || !sign || !deviceId) return "";
    const params = new URLSearchParams({
      token,
      aid: FEIGE_WS.aid,
      fpid: FEIGE_WS.fpid,
      device_id: deviceId,
      access_key: FEIGE_WS.access_key,
      device_platform: "web",
      version_code: FEIGE_WS.version_code,
      pigeon_source: "web",
      PIGEON_BIZ_TYPE: "2",
      pigeon_sign: sign,
    });
    return `${FEIGE_WS.host}?${params.toString()}`;
  }

  let wsUrl = synthesizeWsUrl(wsToken, pigeonSign);

  if (!wsUrl && wsToken && wsUrls.length) {
    for (const u of wsUrls) {
      const ustr = String(u);
      if (!ustr.includes(`token=${encodeURIComponent(wsToken)}`) && !ustr.includes(`token=${wsToken}`)) {
        continue;
      }
      const sm = ustr.match(/[?&]pigeon_sign=([^&]+)/);
      if (sm) {
        pigeonSign = decodeURIComponent(sm[1]);
        wsUrl = synthesizeWsUrl(wsToken, pigeonSign);
        break;
      }
    }
  }

  if (!wsUrl) {
    wsUrl =
      wsUrls.find((u) => {
        const ustr = String(u);
        return (
          ustr.includes("ws.fxg.jinritemai.com") &&
          ustr.includes("pigeon_sign=") &&
          wsToken &&
          (ustr.includes(`token=${wsToken}`) || ustr.includes(`token=${encodeURIComponent(wsToken)}`))
        );
      }) || "";
  }

  if (!wsUrl && wsToken && pigeonSign) {
    wsUrl = synthesizeWsUrl(wsToken, pigeonSign);
  }

  if (!wsUrl) {
    wsUrl =
      wsUrls.find((u) => String(u).includes("ws.fxg.jinritemai.com") && String(u).includes("pigeon_sign=")) ||
      wsUrls.find((u) => String(u).includes("ws.fxg.jinritemai.com")) ||
      wsUrls[0] ||
      "";
  }
  const shopId = String(raw.shop_id || cookies.SHOP_ID || "");
  const cookieHeader = Object.entries(cookies)
    .map(([k, v]) => `${k}=${v}`)
    .join("; ");

  let conversationId = process.env.PIGEON_CONVERSATION_ID || raw.conversation_id || "";
  const secUid = process.env.PIGEON_SECURITY_USER_ID || "";
  if (!conversationId && secUid.startsWith("AQ") && shopId) {
    conversationId = `${secUid}:${shopId}::2:1:pigeon`;
  }

  const convShortId = process.env.PIGEON_CONV_SHORT_ID || "";
  const convTicket = process.env.PIGEON_CONV_TICKET || "";

  const frontierMsgServiceId = Number(
    process.env.PIGEON_IM_SERVICE_ID || qt.frontier_msgServiceId || qt.msgServiceId || 0
  );
  const frontierTemaiServiceId = Number(
    process.env.PIGEON_FRONTIER_BIZ_SERVICE_ID || qt.frontier_temaiServiceId || qt.temaiServiceId || 0
  );

  return {
    cookies,
    deviceId,
    shopId,
    wsToken,
    wsUrl,
    cookieHeader,
    pigeonSign,
    userId: String(cookies.uid_tt || shopId || deviceId),
    conversationId,
    convShortId,
    convTicket,
    text: process.env.PIGEON_MESSAGE_TEXT || process.env.FEIGE_MESSAGE_TEXT || "好",
    frontierMsgServiceId,
    frontierTemaiServiceId,
  };
}

function promInvoke(sdk, clientId, buffer, timeoutMs = 60000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`invokeAsync timeout ${timeoutMs}ms`)), timeoutMs);
    try {
      sdk.invokeAsync(clientId, buffer, (err, data) => {
        clearTimeout(timer);
        if (err) reject(err instanceof Error ? err : new Error(String(err)));
        else resolve(data);
      });
    } catch (e) {
      clearTimeout(timer);
      reject(e);
    }
  });
}

function callInitSdk(sdk, buf) {
  if (typeof sdk.initSdkFromBuffer === "function") {
    sdk.initSdkFromBuffer(buf);
    return undefined;
  }
  if (typeof sdk.initSdk !== "function") throw new Error("initSdk/initSdkFromBuffer missing");
  try {
    const ret = sdk.initSdk(buf);
    if (ret && typeof ret.then === "function") return ret;
    return undefined;
  } catch (syncErr) {
    return new Promise((resolve, reject) => {
      try {
        sdk.initSdk(buf, (err) => (err ? reject(err) : resolve()));
      } catch {
        reject(syncErr);
      }
    });
  }
}

function buildPacked($root, { clientId, cmdId, requestBytes, accessToken }) {
  const taskId = `${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  const ctx = { taskId, cmdId, clientId };
  if (accessToken !== undefined && accessToken !== null && accessToken !== "") {
    ctx.accessToken = accessToken;
  }
  const msg = $root.packedMessage.PackedMessage.create({
    context: ctx,
    request: requestBytes,
    status: 0,
  });
  return $root.packedMessage.PackedMessage.encode(msg).finish();
}

async function invokeSimple($root, sdk, clientId, { cmdId, requestBytes, accessToken, label, timeoutMs = 60000 }) {
  const packed = buildPacked($root, { clientId, cmdId, requestBytes, accessToken });
  const resp = await promInvoke(sdk, clientId, Buffer.from(packed), timeoutMs);
  const dec = decodePacked($root, resp);
  return {
    label,
    cmdId,
    resp_len: resp?.length ?? 0,
    resp_hex_head: bufHexPreview(resp, 48),
    status: dec?.status,
    status_label: dec?.status === 1 ? "Success" : dec?.status === 0 ? "Failed" : String(dec?.status),
    error: dec?.error || dec?.response?.error || null,
    code: dec?.code ?? dec?.response?.code,
    context_access_token: dec?.context?.accessToken ? `${dec.context.accessToken.slice(0, 12)}...` : null,
    response_body_len: dec?.response?.body?.length ?? 0,
    inner_169_hex: find169Inner(resp),
  };
}

function decodePacked($root, buf) {
  try {
    return $root.packedMessage.PackedMessage.decode(buf);
  } catch {
    return null;
  }
}

function extractInnerFromPacked($root, buf) {
  const dec = decodePacked($root, buf);
  if (!dec) return { inner169: find169Inner(buf), packed: null };

  const blobs = [dec.request, dec.response?.body].filter(Boolean);
  for (const blob of blobs) {
    const hit = find169Inner(blob);
    if (hit) return { inner169: hit, packed: dec, via: "packed_field" };
  }

  if (dec.response?.body?.length) {
    try {
      const biz = $root.biz.pigeon.im.IMCreateMessage.Response.decode(dec.response.body);
      const bizStr = JSON.stringify(biz);
      const m = bizStr.match(/[0-9a-f]{338}/i);
      if (m) return { inner169: m[0].slice(0, 338), packed: dec, via: "biz_response" };
    } catch {}
  }

  return { inner169: find169Inner(buf), packed: dec, via: "raw_scan" };
}

function bufHexPreview(buf, n = 32) {
  if (!buf || !buf.length) return "";
  return Buffer.from(buf).subarray(0, n).toString("hex");
}

function extractAccessTokensFromInit($root, dec) {
  const out = [];
  const push = (label, val) => {
    if (val && typeof val === "string" && val.length > 8) out.push([label, val]);
  };
  push("packed_context", dec?.context?.accessToken);
  if (dec?.response?.body?.length) {
    const body = dec.response.body;
    try {
      const biz = $root.biz.pigeon.im.IMInitMessage.Response.decode(body);
      push("iminit_biz_response_json", JSON.stringify(biz?.biz_response || {}));
      if (biz?.biz_response?.accessToken) push("iminit_biz_accessToken", biz.biz_response.accessToken);
    } catch {}
    try {
      const api = $root.biz.im.im_api.ResponseBody.decode(body);
      push("im_api_accessToken", api?.access_token || api?.accessToken);
    } catch {}
    // scan utf8 strings in body that look like IM tokens
    const txt = Buffer.from(body).toString("utf8");
    for (const m of txt.matchAll(/[A-Za-z0-9_-]{20,120}/g)) {
      if (m[0].length >= 24) push(`body_str_${out.length}`, m[0]);
    }
  }
  return out;
}

function extractCreateUserAccess($root, buf) {
  const dec = decodePacked($root, buf);
  const out = { accessToken: "", dec, via: null, tokens: [] };
  const push = (label, val) => {
    if (val && typeof val === "string" && val.length > 8) out.tokens.push([label, val]);
  };
  push("packed_context", dec?.context?.accessToken);
  if (dec?.response?.body?.length) {
    try {
      const biz = $root.biz.pigeon.user.CreatePigeonUserMessage.Response.decode(dec.response.body);
      push("create_user_biz", biz?.accessToken);
      if (biz?.accessToken) {
        out.accessToken = biz.accessToken;
        out.via = "create_user_biz";
      }
    } catch {}
  }
  if (!out.accessToken) {
    out.accessToken = dec?.context?.accessToken || "";
    if (out.accessToken) out.via = "packed_context";
  }
  return out;
}

function decodeInitAccess($root, initResp) {
  const dec = decodePacked($root, initResp);
  const tokens = extractAccessTokensFromInit($root, dec);
  let accessToken = dec?.context?.accessToken || "";
  for (const [label, tok] of tokens) {
    if (label.includes("access") && tok && !tok.startsWith("{")) {
      accessToken = tok;
      break;
    }
  }
  return { dec, accessToken, tokens };
}

function shannonEntropy(buf) {
  const freq = new Array(256).fill(0);
  for (const b of buf) freq[b]++;
  let ent = 0;
  for (const f of freq) {
    if (!f) continue;
    const p = f / buf.length;
    ent -= p * Math.log2(p);
  }
  return ent;
}

function find169Inner(buf) {
  const b = Buffer.from(buf);
  for (let i = 0; i + 169 <= b.length; i++) {
    const slice = b.subarray(i, i + 169);
    if (slice[0] === 0x23 && slice[1] === 0x1a) return slice.toString("hex");
  }
  return null;
}

function find169ByEntropy(buf) {
  const b = Buffer.from(buf);
  for (let i = 0; i + 169 <= b.length; i++) {
    const slice = b.subarray(i, i + 169);
    const body = slice.subarray(8);
    if (body.length !== 161) continue;
    const hdr0 = slice.readUInt32LE(0);
    const hdr1 = slice.readUInt32LE(4);
    if (hdr0 === 0 && hdr1 === 0) continue;
    if (shannonEntropy(body) >= 6.0) return { inner169: slice.toString("hex"), via: `entropy@${i}` };
  }
  return { inner169: null, via: null };
}

function find169InnerRelaxed(buf) {
  const hit = find169Inner(buf);
  if (hit) return { inner169: hit, via: "magic_231a" };
  const ent = find169ByEntropy(buf);
  if (ent.inner169) return ent;
  const b = Buffer.from(buf);
  const txt = b.toString("latin1");
  for (const m of txt.matchAll(/[A-Za-z0-9+/]{220,240}={0,2}/g)) {
    try {
      const raw = Buffer.from(m[0], "base64");
      if (raw.length === 169) return { inner169: raw.toString("hex"), via: "b64_169" };
    } catch {}
  }
  return { inner169: null, via: null };
}

function scanAnyInner($root, buf) {
  if (!buf || !buf.length) return { inner169: null, via: null };
  const direct = find169InnerRelaxed(buf);
  if (direct.inner169) return direct;
  const dec = decodePacked($root, buf);
  if (dec) {
    for (const blob of [dec.request, dec.response?.body].filter(Boolean)) {
      const hit = find169InnerRelaxed(blob);
      if (hit.inner169) return { ...hit, via: `packed_${hit.via}` };
    }
  }
  return { inner169: null, via: null };
}

function scanPushLog($root, entries) {
  for (const row of entries || []) {
    if (row.inner_169_hex) {
      return { inner169: row.inner_169_hex, via: row.inner_via || "push_log" };
    }
  }
  return { inner169: null, via: null };
}

function analyzePushBuffer($root, buf) {
  const out = { cmdId: null, status: null, req_len: 0, body_len: 0 };
  const dec = decodePacked($root, buf);
  if (dec?.context) {
    out.cmdId = dec.context.cmdId ?? null;
    out.status = dec.status ?? null;
    out.req_len = dec.request?.length ?? 0;
    out.body_len = dec.response?.body?.length ?? 0;
  }
  return out;
}

function parseCreateMessageMeta($root, createResp) {
  const dec = decodePacked($root, createResp);
  const out = { client_id: null, server_id: null, flight_status: null };
  if (!dec?.response?.body?.length) return out;
  try {
    const biz = $root.biz.pigeon.im.IMCreateMessage.Response.decode(dec.response.body);
    const msg = biz?.biz_response?.message;
    if (msg) {
      out.client_id = msg.client_id || msg.client_message_id || null;
      if (msg.server_id != null) out.server_id = String(msg.server_id);
      if (msg.flight_status != null) out.flight_status = msg.flight_status;
    }
  } catch {}
  return out;
}

function parseConversationMeta($root, respBuf) {
  const out = { ticket: null, short_id: null, has_ticket: false };
  const dec = decodePacked($root, respBuf);
  if (!dec?.response?.body?.length) return out;
  try {
    const biz = $root.biz.pigeon.im.IMGetConversation.Response.decode(dec.response.body);
    const conv = biz?.biz_response?.conversation;
    if (conv) {
      out.ticket = conv.ticket || null;
      out.has_ticket = Boolean(conv.ticket);
      if (conv.short_id != null) out.short_id = String(conv.short_id);
    }
  } catch {}
  return out;
}

function decodePushCmd($root, buf) {
  const dec = decodePacked($root, buf);
  const cmdId = dec?.context?.cmdId ?? null;
  const out = { cmdId, body_len: dec?.response?.body?.length ?? 0 };
  if (cmdId === 11345 && dec?.response?.body?.length) {
    try {
      const push = $root.biz.pigeon.im.IMMessageSendPushMessage.Response.decode(dec.response.body);
      const msg = push?.biz_response?.message;
      if (msg) {
        out.client_id = msg.client_message_id || msg.client_id || null;
        out.flight_status = msg.flight_status ?? null;
      }
    } catch {}
  }
  return out;
}

function extractCreateMessageInner($root, buf) {
  const base = extractInnerFromPacked($root, buf);
  if (base.inner169) return base;

  const dec = decodePacked($root, buf);
  if (dec?.response?.body?.length) {
    const body = dec.response.body;
    const relaxed = find169InnerRelaxed(body);
    if (relaxed.inner169) return { ...base, inner169: relaxed.inner169, via: relaxed.via };

    try {
      const biz = $root.biz.pigeon.im.IMCreateMessage.Response.decode(body);
      const msg = biz?.biz_response?.message;
      if (msg) {
        const ext = msg.ext || {};
        for (const [k, v] of Object.entries(ext)) {
          if (typeof v !== "string") continue;
          if (/^[0-9a-f]{338}$/i.test(v)) return { ...base, inner169: v.slice(0, 338), via: `ext_${k}` };
          try {
            const raw = Buffer.from(v, "base64");
            if (raw.length === 169) return { ...base, inner169: raw.toString("hex"), via: `ext_b64_${k}` };
          } catch {}
        }
        const encoded = $root.biz.pigeon.im.MessageBody.encode(msg).finish();
        const hit = find169InnerRelaxed(encoded);
        if (hit.inner169) return { ...base, inner169: hit.inner169, via: `message_body_${hit.via}` };
      }
    } catch {}
  }
  return base;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isValidInner169Hex(hex) {
  if (typeof hex !== "string" || hex.length !== 338) return false;
  try {
    const b = Buffer.from(hex, "hex");
    if (b.length !== 169) return false;
    const hdr0 = b.readUInt32LE(0);
    const hdr1 = b.readUInt32LE(4);
    if (hdr0 === 0 && hdr1 === 0) return false;
    return shannonEntropy(b.subarray(8)) >= 6.0;
  } catch {
    return false;
  }
}

function pickInner169(candidates) {
  for (const [hex, via] of candidates) {
    if (isValidInner169Hex(hex)) return { inner169: hex, via };
  }
  return { inner169: null, via: null };
}

function resolveInstallPath() {
  const candidates = [
    process.env.PIGEON_FEIGE_INSTALL,
    "E:\\feige-electron\\抖店工作台\\1.1.7",
    process.env.PIGEON_RUST_SDK_INSTALL,
    SDK_ROOT,
  ].filter(Boolean);
  for (const p of candidates) {
    if (existsSync(p)) return p;
  }
  return SDK_ROOT;
}

function seedTtnetStorage(storagePath, liveStoragePath, installPath = "") {
  const ttnetDir = join(storagePath, "logs", "ttnet");
  const prefsDir = join(ttnetDir, "prefs");
  mkdirSync(prefsDir, { recursive: true });

  const localPrefs = join(prefsDir, "local_prefs.json");
  const directPrefs = {
    net: {
      proxy: { mode: "direct" },
      http_server_properties: { servers: [], version: 5 },
    },
  };

  const copyIfMissing = (src, dst) => {
    if (existsSync(src) && !existsSync(dst)) copyFileSync(src, dst);
  };

  const liveTtnet =
    liveStoragePath && existsSync(liveStoragePath) ? join(liveStoragePath, "logs", "ttnet") : "";
  if (liveTtnet && existsSync(liveTtnet)) {
    for (const name of ["server.json", "tt_net_config.config"]) {
      copyIfMissing(join(liveTtnet, name), join(ttnetDir, name));
    }
    copyIfMissing(join(liveTtnet, "prefs", "local_prefs.json"), localPrefs);
  }

  const installTtnetCache =
    installPath && existsSync(installPath) ? join(installPath, "TTNetCache", "prefs", "local_prefs.json") : "";
  if (installTtnetCache) {
    copyIfMissing(installTtnetCache, localPrefs);
  }

  if (!existsSync(localPrefs)) {
    writeFileSync(localPrefs, JSON.stringify(directPrefs, null, 2));
  } else {
    try {
      const current = JSON.parse(readFileSync(localPrefs, "utf8"));
      current.net = { ...(current.net || {}), ...directPrefs.net };
      writeFileSync(localPrefs, JSON.stringify(current, null, 2));
    } catch {
      writeFileSync(localPrefs, JSON.stringify(directPrefs, null, 2));
    }
  }
}

function buildWsConnectConfig(sess) {
  const hostMode = process.env.PIGEON_WS_HOST || "jinritemai"; // fxg | jinritemai
  const wsHost = hostMode === "jinritemai" ? FEIGE_WS.altHost : FEIGE_WS.host;
  const accessKey =
    hostMode === "jinritemai" ? FEIGE_WS.alt_access_key : FEIGE_WS.access_key;

  const wsHttpQuery = {
    token: sess.wsToken,
    aid: FEIGE_WS.aid,
    fpid: FEIGE_WS.fpid,
    device_id: sess.deviceId,
    access_key: accessKey,
    device_platform: hostMode === "jinritemai" ? "pc" : "web",
    version_code: FEIGE_WS.version_code,
    pigeon_source: hostMode === "jinritemai" ? "pc" : "web",
    PIGEON_BIZ_TYPE: "2",
  };
  if (sess.pigeonSign) wsHttpQuery.pigeon_sign = sess.pigeonSign;

  const wsQueryString = new URLSearchParams(wsHttpQuery).toString();
  const wsFrontierBase = wsHost;
  let wsFrontierFull = sess.wsUrl && sess.wsUrl.includes("?") ? sess.wsUrl : `${wsFrontierBase}?${wsQueryString}`;
  if (hostMode === "jinritemai") {
    wsFrontierFull = `${wsFrontierBase}?${wsQueryString}`;
  }

  const wsHttpHeaders = {
    Cookie: sess.cookieHeader,
    "User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
  };

  // ttnet logs origin_url without query when frontier_url carries ?… — Rust merges http_query separately.
  const frontierMode = process.env.PIGEON_WS_FRONTIER_MODE || "split";
  const wsFrontierConnect =
    frontierMode === "full" ? wsFrontierFull : wsFrontierBase;
  const wsHttpQueryEffective = frontierMode === "full" ? {} : wsHttpQuery;

  return {
    wsHost,
    accessKey,
    hostMode,
    wsFrontierBase,
    wsFrontierFull,
    wsFrontierConnect,
    wsHttpQuery,
    wsHttpQueryEffective,
    wsQueryString,
    wsHttpHeaders,
    frontierMode,
  };
}

async function main() {
  for (const k of ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]) {
    delete process.env[k];
  }
  process.env.NO_PROXY = "*";
  process.env.no_proxy = "*";
  process.env.WINHTTP_PROXY = "";
  process.env.WINHTTP_PROXY_BYPASS = "*";

  const report = {
    session_path: SESSION_PATH,
    api_js: API_JS,
    native_pkg: nativePkg,
    steps: {},
    ok: false,
  };

  if (!existsSync(join(nativePkg, "rust-sdk.win32-x64-msvc.node"))) {
    report.error = "native .node missing — run: python run.py feige-export-sdk";
    console.log(JSON.stringify(report, null, 2));
    process.exit(1);
  }

  const nodeLink = join(rustSdkDir, "rust-sdk.win32-x64-msvc.node");
  if (!existsSync(nodeLink)) {
    copyFileSync(join(nativePkg, "rust-sdk.win32-x64-msvc.node"), nodeLink);
  }

  process.chdir(rustSdkDir);
  process.env.PATH = `${nativePkg};${process.env.PATH || ""}`;
  process.env.NODE_PATH = process.env.NODE_PATH
    ? `${NODE_MODULES};${process.env.NODE_PATH}`
    : NODE_MODULES;
  Module._initPaths();

  const require = createRequire(join(rustSdkDir, "index.js"));
  const sdk = require(join(rustSdkDir, "index.js"));
  const $root = require(API_JS);

  const sess = loadSession();
  report.session = {
    device_id: sess.deviceId,
    shop_id: sess.shopId,
    has_ws_token: Boolean(sess.wsToken),
    ws_token_preview: sess.wsToken ? `${sess.wsToken.slice(0, 8)}...` : null,
    conversation_id: sess.conversationId || null,
    conv_short_id: sess.convShortId || null,
    has_conv_ticket: Boolean(sess.convTicket),
    text: sess.text,
  };

  if (!sess.conversationId && process.env.PIGEON_CREATE_USER_ONLY !== "1") {
    report.error = "conversation_id required — set FEIGE_CONVERSATION_ID or pass via session JSON";
    console.log(JSON.stringify(report, null, 2));
    process.exit(1);
  }

  // --- initSdk (SDK-level) ---
  const probeRoot = join(ROOT, "analysis", "feige_rust_sdk_probe");
  const feigeStorage =
    process.env.PIGEON_FEIGE_RS_SDK ||
    join(process.env.APPDATA || "", "抖店工作台", "rs_sdk");
  const useLiveStorage = process.env.PIGEON_USE_LIVE_RS_SDK === "1";
  const storagePath =
    useLiveStorage && existsSync(feigeStorage) ? feigeStorage : join(probeRoot, "rs_sdk");
  const logPath = join(storagePath, "logs");
  const installPath = resolveInstallPath();
  mkdirSync(logPath, { recursive: true });
  seedTtnetStorage(storagePath, existsSync(feigeStorage) ? feigeStorage : "", installPath);

  const initReq = $root.sdk.InitSDKReq.create({
    storage_path: storagePath,
    install_path: installPath,
    custom_log_path: logPath,
    device_id: sess.deviceId,
    app_id: 1383,
    app_name: "im",
    device_platform: process.env.PIGEON_WS_HOST === "fxg" ? "web" : "pc",
    app_version: "10000",
    enable_tracing: false,
    enable_ttnet_verbose_log: true,
    log_level: 0,
    http_headers: {
      Cookie: sess.cookieHeader,
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    },
  });
  report.steps.initSdk_paths = {
    storagePath,
    logPath,
    installPath,
    sdkRoot: SDK_ROOT,
    use_live_storage: useLiveStorage,
  };
  const initBuf = Buffer.from($root.sdk.InitSDKReq.encode(initReq).finish());
  report.steps.initSdk_bytes = initBuf.length;

  try {
    await callInitSdk(sdk, initBuf);
    report.steps.initSdk = "ok_via_initSdkFromBuffer";
  } catch (e) {
    report.steps.initSdk = `error: ${e}`;
  }

  report.steps.getDevice = sdk.getDevice?.() ?? null;

  // --- createClient ---
  const pushLog = [];
  const pushRawBuffers = [];
  let largestPush = { len: 0, buf: null };
  let encClientId;
  try {
    encClientId = sdk.createClient((err, data) => {
      if (err) {
        pushLog.push({ t: Date.now(), error: String(err) });
        return;
      }
      if (data == null) return;
      try {
        const buf = Buffer.from(data);
        pushRawBuffers.push(buf);
        const hit = scanAnyInner($root, buf);
        const meta = analyzePushBuffer($root, buf);
        if (buf.length > largestPush.len) largestPush = { len: buf.length, buf };
        pushLog.push({
          t: Date.now(),
          len: buf.length,
          head: bufHexPreview(buf, Math.min(buf.length, 64)),
          cmdId: meta.cmdId,
          req_len: meta.req_len,
          body_len: meta.body_len,
          inner_169_hex: hit.inner169,
          inner_via: hit.via,
          push_detail: meta.cmdId === 11345 ? decodePushCmd($root, buf) : null,
        });
      } catch (e) {
        pushLog.push({ t: Date.now(), error: String(e) });
      }
    });
    report.steps.createClient_enc_len = String(encClientId || "").length;
  } catch (e) {
    report.error = `createClient failed: ${e}`;
    console.log(JSON.stringify(report, null, 2));
    process.exit(1);
  }

  let clientId;
  try {
    clientId = decryptHexStr(String(encClientId));
    report.steps.clientId = clientId.slice(0, 12) + "...";
  } catch (e) {
    report.error = `decrypt clientId failed: ${e}`;
    console.log(JSON.stringify(report, null, 2));
    process.exit(1);
  }

  const bizContext = { biz_id: "2" };
  const inboxTypes = [0, 1, 2, 3];

  // --- PigeonCreateUser 11200 (SDK internal accessToken, NOT ws frontier token) ---
  let accessToken = "";
  try {
    const createUserReq = $root.biz.pigeon.user.CreatePigeonUserMessage.Request.create({
      sessionPartitionKey: `persist:${sess.deviceId}`,
      userInfo: $root.biz.pigeon.user.PigeonUserInfo.create({
        id: sess.deviceId,
        screenName: sess.shopId || sess.deviceId,
      }),
      credentials: $root.biz.pigeon.user.PigeonUserCredentials.create({
        cookies: sess.cookieHeader,
      }),
    });
    const createUserInner = $root.biz.pigeon.user.CreatePigeonUserMessage.Request.encode(createUserReq).finish();
    const createUserPacked = buildPacked($root, {
      clientId,
      cmdId: CMD_CREATE_USER,
      requestBytes: createUserInner,
      accessToken: undefined,
    });
    const createUserResp = await promInvoke(sdk, clientId, Buffer.from(createUserPacked));
    const parsedUser = extractCreateUserAccess($root, createUserResp);
    if (parsedUser.accessToken) accessToken = parsedUser.accessToken;
    report.steps.createUser = {
      resp_len: createUserResp?.length ?? 0,
      resp_hex_head: bufHexPreview(createUserResp, 48),
      status: parsedUser.dec?.status,
      error: parsedUser.dec?.error || null,
      access_token_preview: accessToken ? `${accessToken.slice(0, 12)}...` : null,
      access_token_full: accessToken || null,
      access_token_via: parsedUser.via,
      token_candidates: parsedUser.tokens.map(([l, v]) => [l, String(v).slice(0, 24) + "..."]),
    };
  } catch (e) {
    report.steps.createUser = { error: String(e) };
  }

  if (!accessToken) {
    accessToken = sess.wsToken;
    report.steps.createUser_fallback = "ws_token";
  }

  if (process.env.PIGEON_CREATE_USER_ONLY === "1") {
    report.ok = Boolean(accessToken);
    report.access_token = accessToken || null;
    report.access_token_via = report.steps.createUser?.access_token_via || report.steps.createUser_fallback || null;
    console.log(JSON.stringify(report, null, 2));
    process.exit(report.ok ? 0 : 1);
  }

  const wsCfg = buildWsConnectConfig(sess);
  const {
    wsFrontierFull,
    wsFrontierConnect,
    wsHttpQuery,
    wsHttpQueryEffective,
    wsQueryString,
    wsHttpHeaders,
    frontierMode,
    hostMode,
    accessKey,
  } = wsCfg;

  const authMode = process.env.PIGEON_WS_AUTH_MODE || "session";
  const imAuthType = authMode === "token" ? 2 : 1;
  const imInitToken = authMode === "token" ? accessToken : sess.wsToken;
  const hybridLinkModel = process.env.PIGEON_HYBRID_LINK_MODEL || "imcloud";
  const imServiceId = sess.frontierMsgServiceId || Number(process.env.PIGEON_IM_SERVICE_ID || 1);
  const frontierBizServiceId =
    sess.frontierTemaiServiceId || Number(process.env.PIGEON_FRONTIER_BIZ_SERVICE_ID || 0);

  const imInitOptions = $root.biz.im.im_config.IMInitOptions.create({
    app_id: 1383,
    token: imInitToken,
    device_id: sess.deviceId,
    user_id: sess.deviceId,
    sec_uid: sess.deviceId,
    inbox_types: inboxTypes,
    auth_type: imAuthType,
    channel: hostMode === "jinritemai" ? "pc" : "web",
    device_platform: hostMode === "jinritemai" ? "pc" : "web",
    version_code: FEIGE_WS.version_code,
    api_url: "https://im.jinritemai.com",
    frontier_url: wsFrontierConnect,
    app_key: accessKey,
    headers: wsHttpHeaders,
    http_headers: wsHttpHeaders,
    http_query: wsHttpQueryEffective,
    extended: {
      ws_full_url: wsFrontierFull,
      access_key: accessKey,
      frontier_url: wsFrontierConnect,
      ws_query: wsQueryString,
    },
    product_id: 92,
    service: imServiceId,
    method: 1,
    session_id: sess.deviceId,
    hybrid_link_model: hybridLinkModel,
    enable_sec_uid: true,
    enable_sec_uid_inbox_types: inboxTypes,
    need_pull: true,
    disable_init_pull: false,
    enable_conversation_chain_v2: true,
    enable_unread_count_calc_v2: true,
  });
  if (frontierBizServiceId > 0) {
    imInitOptions.frontier_biz_service_id = frontierBizServiceId;
  }
  report.steps.imInit_options = {
    user_id: sess.deviceId,
    device_id: sess.deviceId,
    auth_type: imAuthType,
    auth_mode: authMode,
    host_mode: hostMode,
    hybrid_link_model: hybridLinkModel,
    service: imServiceId,
    frontier_biz_service_id: frontierBizServiceId || null,
    access_key_preview: `${accessKey.slice(0, 8)}...`,
    token_preview: imInitToken ? `${imInitToken.slice(0, 8)}...` : null,
    frontier_mode: frontierMode,
    frontier_url: wsFrontierConnect.slice(0, 160),
    frontier_full_preview: wsFrontierFull.slice(0, 120),
    http_query_keys: Object.keys(wsHttpQueryEffective),
    ws_query_len: wsQueryString.length,
  };

  const imInitReq = $root.biz.pigeon.im.IMInitMessage.Request.create({
    biz_context: bizContext,
    biz_request: $root.biz.pigeon.im.IMInitMessage.BizRequest.create({ options: imInitOptions }),
  });
  const imInitInner = $root.biz.pigeon.im.IMInitMessage.Request.encode(imInitReq).finish();
  const imInitPacked = buildPacked($root, {
    clientId,
    cmdId: CMD_INIT,
    requestBytes: imInitInner,
    accessToken,
  });

  let initDec = null;
  try {
    const initResp = await promInvoke(sdk, clientId, Buffer.from(imInitPacked));
    const parsed = decodeInitAccess($root, initResp);
    initDec = parsed.dec;
    if (parsed.accessToken) accessToken = parsed.accessToken;
    report.steps.imInit = {
      resp_len: initResp?.length ?? 0,
      resp_hex_head: bufHexPreview(initResp),
      status: initDec?.status,
      status_label: initDec?.status === 1 ? "Success" : initDec?.status === 0 ? "Failed" : String(initDec?.status),
      error: initDec?.error || null,
      code: initDec?.code,
      context_access_token: initDec?.context?.accessToken ? `${initDec.context.accessToken.slice(0, 8)}...` : null,
      access_token_preview: accessToken ? `${accessToken.slice(0, 8)}...` : null,
      token_candidates: parsed.tokens.map(([l, v]) => [l, String(v).slice(0, 24) + (String(v).length > 24 ? "..." : "")]),
      response_body_len: initDec?.response?.body?.length ?? 0,
      response_body_hex: initDec?.response?.body?.length ? bufHexPreview(initDec.response.body, 64) : null,
    };
  } catch (e) {
    report.steps.imInit = { error: String(e) };
  }

  async function updateNetReconnect(label = "update_net_reconnect") {
    const updateInner = $root.biz.pigeon.im.IMUpdateNetOptionAndReconnectMessage.Request.encode(
      $root.biz.pigeon.im.IMUpdateNetOptionAndReconnectMessage.Request.create({
        biz_context: bizContext,
        biz_request: $root.biz.pigeon.im.IMUpdateNetOptionAndReconnectMessage.BizRequest.create({
          token: imInitToken,
          http_headers: wsHttpHeaders,
          http_query: wsHttpQueryEffective,
          extended: {
            ws_full_url: wsFrontierFull,
            access_key: accessKey,
            frontier_url: wsFrontierConnect,
            ws_query: wsQueryString,
          },
        }),
      })
    ).finish();
    return invokeSimple($root, sdk, clientId, {
      cmdId: CMD_UPDATE_NET,
      requestBytes: updateInner,
      accessToken,
      label,
      timeoutMs: 30000,
    });
  }

  try {
    report.steps.updateNetReconnect = await updateNetReconnect();
  } catch (e) {
    report.steps.updateNetReconnect = { error: String(e) };
  }

  // --- post-init WS handshake (Rust SDK must connect frontier before 11327) ---
  try {
    const wsStateInner = $root.biz.pigeon.im.IMGetWsStateMessage.Request.encode(
      $root.biz.pigeon.im.IMGetWsStateMessage.Request.create({
        biz_context: bizContext,
        biz_request: {},
      })
    ).finish();
    report.steps.wsState = await invokeSimple($root, sdk, clientId, {
      cmdId: CMD_WS_STATE,
      requestBytes: wsStateInner,
      accessToken,
      label: "ws_state",
      timeoutMs: 15000,
    });
  } catch (e) {
    report.steps.wsState = { error: String(e) };
  }

  const wsReady = !report.steps.wsState?.error;
  const hadWsError = pushLog.some((r) => r.cmdId === 11304);
  const wsWaitInner = $root.biz.pigeon.im.IMGetWsStateMessage.Request.encode(
    $root.biz.pigeon.im.IMGetWsStateMessage.Request.create({
      biz_context: bizContext,
      biz_request: {},
    })
  ).finish();
  try {
    report.steps.wsWait = await invokeSimple($root, sdk, clientId, {
      cmdId: CMD_WS_WAIT,
      requestBytes: wsWaitInner,
      accessToken,
      label: hadWsError ? "ws_wait_after_error" : "ws_wait",
      timeoutMs: hadWsError ? 45000 : 15000,
    });
  } catch (e) {
    report.steps.wsWait = { error: String(e) };
    if (hadWsError) {
      try {
        report.steps.updateNetReconnect2 = await updateNetReconnect("update_net_after_ws_error");
        report.steps.wsWait2 = await invokeSimple($root, sdk, clientId, {
          cmdId: CMD_WS_WAIT,
          requestBytes: wsWaitInner,
          accessToken,
          label: "ws_wait_retry",
          timeoutMs: 45000,
        });
      } catch (e2) {
        report.steps.wsWait2 = { error: String(e2) };
      }
    }
  }

  // Force frontier-first send strategy (avoid HTTP ticket/decode path when cookies stale).
  try {
    const strategyInner = $root.biz.pigeon.im.IMSetRequestStrategy.Request.encode(
      $root.biz.pigeon.im.IMSetRequestStrategy.Request.create({
        biz_context: bizContext,
        biz_request: {
          max_frontier_times: Number(process.env.PIGEON_MAX_FRONTIER_TIMES || 10),
          max_http_times: Number(process.env.PIGEON_MAX_HTTP_TIMES || 0),
          http_loop_interval_ms: Number(process.env.PIGEON_HTTP_LOOP_MS || 1000),
        },
      })
    ).finish();
    report.steps.setRequestStrategy = await invokeSimple($root, sdk, clientId, {
      cmdId: CMD_SET_STRATEGY,
      requestBytes: strategyInner,
      accessToken,
      label: "set_request_strategy",
      timeoutMs: 15000,
    });
  } catch (e) {
    report.steps.setRequestStrategy = { error: String(e) };
  }

  // --- seed local conversation cache before 11327 ---
  try {
    const getConvInner = $root.biz.pigeon.im.IMGetConversation.Request.encode(
      $root.biz.pigeon.im.IMGetConversation.Request.create({
        biz_context: bizContext,
        biz_request: { conversation_id: sess.conversationId },
      })
    ).finish();
    report.steps.getConversation = await invokeSimple($root, sdk, clientId, {
      cmdId: CMD_GET_CONV,
      requestBytes: getConvInner,
      accessToken,
      label: "get_conversation",
      timeoutMs: 30000,
    });
  } catch (e) {
    report.steps.getConversation = { error: String(e) };
  }

  try {
    const listInner = $root.biz.pigeon.im.IMGetConversationList.Request.encode(
      $root.biz.pigeon.im.IMGetConversationList.Request.create({
        biz_context: bizContext,
        biz_request: {
          inbox_type: 0,
          conversation_id_list: [sess.conversationId],
        },
      })
    ).finish();
    report.steps.getConversationList = await invokeSimple($root, sdk, clientId, {
      cmdId: CMD_GET_CONV_LIST,
      requestBytes: listInner,
      accessToken,
      label: "get_conversation_list",
      timeoutMs: 30000,
    });
  } catch (e) {
    report.steps.getConversationList = { error: String(e) };
  }

  try {
    const fetchInner = $root.biz.pigeon.im.IMCloudFetchConversationList.Request.encode(
      $root.biz.pigeon.im.IMCloudFetchConversationList.Request.create({
        biz_context: bizContext,
        biz_request: {
          conversation_info_list: [
            {
              conversation_id: sess.conversationId,
              short_id: sess.convShortId || "0",
              type: 2,
            },
          ],
        },
      })
    ).finish();
    report.steps.fetchConversation = await invokeSimple($root, sdk, clientId, {
      cmdId: CMD_FETCH_CONV,
      requestBytes: fetchInner,
      accessToken,
      label: "fetch_conversation",
      timeoutMs: 45000,
    });
  } catch (e) {
    report.steps.fetchConversation = { error: String(e) };
  }

  if (!report.steps.wsWait?.error) {
    try {
      const refreshInner = $root.biz.pigeon.im.IMCloudRefreshLocalConversations.Request.encode(
        $root.biz.pigeon.im.IMCloudRefreshLocalConversations.Request.create({
          biz_context: bizContext,
          biz_request: {},
        })
      ).finish();
      report.steps.refreshConversations = await invokeSimple($root, sdk, clientId, {
        cmdId: CMD_REFRESH_CONV,
        requestBytes: refreshInner,
        accessToken,
        label: "refresh_conversations",
        timeoutMs: 45000,
      });
    } catch (e) {
      report.steps.refreshConversations = { error: String(e) };
    }

    try {
      const pullInner = $root.biz.pigeon.im.IMCloudPullLatestMessagesByConversation.Request.encode(
        $root.biz.pigeon.im.IMCloudPullLatestMessagesByConversation.Request.create({
          biz_context: bizContext,
          biz_request: { conversation_id: sess.conversationId },
        })
      ).finish();
      report.steps.pullLatestMessages = await invokeSimple($root, sdk, clientId, {
        cmdId: CMD_PULL_LATEST,
        requestBytes: pullInner,
        accessToken,
        label: "pull_latest_messages",
        timeoutMs: 45000,
      });
    } catch (e) {
      report.steps.pullLatestMessages = { error: String(e) };
    }

    try {
      const getConvInner2 = $root.biz.pigeon.im.IMGetConversation.Request.encode(
        $root.biz.pigeon.im.IMGetConversation.Request.create({
          biz_context: bizContext,
          biz_request: { conversation_id: sess.conversationId },
        })
      ).finish();
      report.steps.getConversationAfterPull = await invokeSimple($root, sdk, clientId, {
        cmdId: CMD_GET_CONV,
        requestBytes: getConvInner2,
        accessToken,
        label: "get_conversation_after_pull",
        timeoutMs: 30000,
      });
      if (report.steps.getConversationAfterPull?.response_body_len) {
        try {
          const packed = buildPacked($root, {
            clientId,
            cmdId: CMD_GET_CONV,
            requestBytes: getConvInner2,
            accessToken,
          });
          const convResp = await promInvoke(sdk, clientId, Buffer.from(packed), 30000);
          report.steps.conversationMeta = parseConversationMeta($root, convResp);
        } catch (e) {
          report.steps.conversationMeta = { error: String(e) };
        }
      }
    } catch (e) {
      report.steps.getConversationAfterPull = { error: String(e) };
    }
  }

  report.steps.push_log = pushLog.slice(-12);

  await sleep(wsReady ? 2000 : 5000);

  // --- PigeonIMCreateMessage 11327 ---
  const clientMsgId = randomUUID();
  const createReq = $root.biz.pigeon.im.IMCreateMessage.Request.create({
    biz_context: bizContext,
    biz_request: {
      conversation_id: sess.conversationId,
      type: 7,
      content: sess.text,
      client_message_id: clientMsgId,
      insert: true,
      ext: {
        "s:client_message_id": clientMsgId,
        "s:biz_aid": "1383",
      },
    },
  });
  const createInner = $root.biz.pigeon.im.IMCreateMessage.Request.encode(createReq).finish();

  async function tryCreateMessage(tokenLabel, tokenValue) {
    const createPacked = buildPacked($root, {
      clientId,
      cmdId: CMD_CREATE,
      requestBytes: createInner,
      accessToken: tokenValue,
    });
    const createResp = await promInvoke(sdk, clientId, Buffer.from(createPacked), 90000);
    const dec = decodePacked($root, createResp);
    const extracted = extractCreateMessageInner($root, createResp);
    return {
      token_label: tokenLabel,
      resp_len: createResp?.length ?? 0,
      resp_hex_head: bufHexPreview(createResp, 48),
      status: dec?.status,
      status_label: dec?.status === 1 ? "Success" : dec?.status === 0 ? "Failed" : String(dec?.status),
      error: dec?.error || dec?.response?.error || null,
      code: dec?.code ?? dec?.response?.code,
      inner_169_hex: extracted.inner169,
      inner_via: extracted.via,
      response_body_len: dec?.response?.body?.length ?? 0,
      response_body_hex: dec?.response?.body?.length ? bufHexPreview(dec.response.body, 96) : null,
      ok: Boolean(
        extracted.inner169 ||
          (!dec?.error && dec?.status === 1 && (dec?.response?.body?.length ?? 0) > 0)
      ),
      _raw_resp: createResp,
    };
  }

  async function invokeAndScan(label, cmdId, requestBytes, tokenValue, timeoutMs = 60000) {
    const pushBefore = pushLog.length;
    const packed = buildPacked($root, {
      clientId,
      cmdId,
      requestBytes,
      accessToken: tokenValue,
    });
    const resp = await promInvoke(sdk, clientId, Buffer.from(packed), timeoutMs);
    await sleep(3000);
    const dec = decodePacked($root, resp);
    const fromResp = scanAnyInner($root, resp);
    const fromPush = scanPushLog($root, pushLog.slice(pushBefore));
    const picked = pickInner169([
      [fromResp.inner169, fromResp.via],
      [fromPush.inner169, fromPush.via],
    ]);
    const inner169 = picked.inner169;
    const innerVia = picked.via;
    return {
      label,
      cmdId,
      resp_len: resp?.length ?? 0,
      resp_hex_head: bufHexPreview(resp, 48),
      status: dec?.status,
      error: dec?.error || dec?.response?.error || null,
      response_body_len: dec?.response?.body?.length ?? 0,
      inner_169_hex: inner169,
      inner_via: innerVia,
      push_new: pushLog.length - pushBefore,
      ok: Boolean(inner169 || (!dec?.error && dec?.status === 1)),
    };
  }

  try {
    const tokenCandidates = [
      ["create_user_token", accessToken],
      ["omit", undefined],
      ["ws_token", sess.wsToken],
    ];
    if (!tokenCandidates.length) {
      tokenCandidates.push(["ws_token", sess.wsToken]);
    }

    let create = null;
    for (const [label, tok] of tokenCandidates) {
      create = await tryCreateMessage(label, tok);
      if (create.inner_169_hex || create.ok) {
        break;
      }
      if (create.error && !String(create.error).toLowerCase().includes("access token")) {
        break;
      }
    }
    report.steps.createMessage = create || { error: "no token candidates" };
    const createMeta = parseCreateMessageMeta($root, create?._raw_resp);
    report.steps.createMessageMeta = createMeta;
    delete report.steps.createMessage?._raw_resp;

    // --- post-create: trigger WS send path for 169B inner ---
    let innerHex = create?.inner_169_hex || null;
    let innerVia = create?.inner_via || null;
    await sleep(2500);
    for (const row of pushLog.slice(-30)) {
      if (row.inner_169_hex) {
        innerHex = row.inner_169_hex;
        innerVia = row.inner_via || "push_after_create";
        break;
      }
    }
    const sendClientId = createMeta.client_id || clientMsgId;
    report.steps.send_client_id = sendClientId;

    if (!innerHex) {
      for (const row of pushLog.slice(-30)) {
        if (row.inner_169_hex) {
          innerHex = row.inner_169_hex;
          innerVia = row.inner_via || "push_after_create";
          break;
        }
        if (row.cmdId === 11304 && row.len > 500) {
          report.steps.ws_error_push = { len: row.len, cmdId: row.cmdId };
        }
      }
    }

    try {
      const getMsgInner = $root.biz.pigeon.im.IMGetMessage.Request.encode(
        $root.biz.pigeon.im.IMGetMessage.Request.create({
          biz_context: bizContext,
          biz_request: {
            conversation_id: sess.conversationId,
            client_message_id: sendClientId,
          },
        })
      ).finish();
      report.steps.getMessage = await invokeAndScan(
        "get_message",
        CMD_GET_MESSAGE,
        getMsgInner,
        accessToken,
        30000
      );
      if (!innerHex && report.steps.getMessage.inner_169_hex) {
        innerHex = report.steps.getMessage.inner_169_hex;
        innerVia = report.steps.getMessage.inner_via;
      }
    } catch (e) {
      report.steps.getMessage = { error: String(e) };
    }

    if (create?.ok && accessToken) {
      const flightMode = process.env.PIGEON_FLIGHT_MODE || "created_inflight";
      const flightSteps = [];
      if (flightMode === "preparing_inflight" || flightMode === "all") {
        flightSteps.push({ label: "set_flight_preparing", status: 1 });
      }
      if (flightMode !== "inflight_only") {
        flightSteps.push({ label: "set_flight_created", status: 0 });
      }
      flightSteps.push({ label: "set_flight_inflight", status: 2 });

      for (const step of flightSteps) {
        try {
          const flightInner = $root.biz.pigeon.im.IMSetMessageFlightStatus.Request.encode(
            $root.biz.pigeon.im.IMSetMessageFlightStatus.Request.create({
              biz_context: bizContext,
              biz_request: {
                conversation_id: sess.conversationId,
                client_message_id: sendClientId,
                flight_status: step.status,
              },
            })
          ).finish();
          const result = await invokeAndScan(
            step.label,
            CMD_SET_FLIGHT,
            flightInner,
            accessToken,
            30000
          );
          report.steps[step.label] = result;
          if (!innerHex && result.inner_169_hex) {
            innerHex = result.inner_169_hex;
            innerVia = result.inner_via;
          }
          if (step.status === 2 && !innerHex) {
            const pushBeforeWs = pushLog.length;
            await sleep(15000);
            const fromPush = scanPushLog($root, pushLog.slice(pushBeforeWs));
            const picked = pickInner169([[fromPush.inner169, fromPush.via]]);
            if (picked.inner169) {
              innerHex = picked.inner169;
              innerVia = picked.via || "push_after_flight_inflight";
            }
            for (const row of pushLog.slice(pushBeforeWs)) {
              if (row.cmdId === CMD_MESSAGE_SEND_PUSH) {
                report.steps.message_send_push = {
                  len: row.len,
                  cmdId: row.cmdId,
                  detail: row.push_detail,
                };
              }
            }
          }
        } catch (e) {
          report.steps[step.label] = { error: String(e) };
        }
      }

      try {
        const sendInner = $root.biz.pigeon.im.IMCloudSendMessage.Request.encode(
          $root.biz.pigeon.im.IMCloudSendMessage.Request.create({
            biz_context: bizContext,
            biz_request: {
              conversation_id: sess.conversationId,
              client_message_id: sendClientId,
            },
          })
        ).finish();
        report.steps.cloudSendMessage = await invokeAndScan(
          "cloud_send",
          CMD_CLOUD_SEND,
          sendInner,
          accessToken,
          90000
        );
        if (!innerHex && report.steps.cloudSendMessage.inner_169_hex) {
          innerHex = report.steps.cloudSendMessage.inner_169_hex;
          innerVia = report.steps.cloudSendMessage.inner_via;
        }
      } catch (e) {
        report.steps.cloudSendMessage = { error: String(e) };
      }

      if (!innerHex) {
        try {
          const swcId = randomUUID();
          const swcInner = $root.biz.pigeon.im.IMCloudSendMessageWithCreate.Request.encode(
            $root.biz.pigeon.im.IMCloudSendMessageWithCreate.Request.create({
              biz_context: bizContext,
              biz_request: {
                conversation_id: sess.conversationId,
                type: 7,
                content: sess.text,
                client_message_id: swcId,
                ext: {
                  "s:client_message_id": swcId,
                  "s:biz_aid": "1383",
                },
              },
            })
          ).finish();
          report.steps.sendWithCreate = await invokeAndScan(
            "send_with_create",
            CMD_SEND_WITH_CREATE,
            swcInner,
            accessToken,
            90000
          );
          if (!innerHex && report.steps.sendWithCreate.inner_169_hex) {
            innerHex = report.steps.sendWithCreate.inner_169_hex;
            innerVia = report.steps.sendWithCreate.inner_via;
          }
        } catch (e) {
          report.steps.sendWithCreate = { error: String(e) };
        }
      }
    }

    if (!innerHex) {
      for (const row of pushLog) {
        if (row.inner_169_hex) {
          innerHex = row.inner_169_hex;
          innerVia = row.inner_via || "push_log";
          break;
        }
        if (row.cmdId === 11345 && row.len > 1000) {
          report.steps.message_send_push = { len: row.len, cmdId: row.cmdId };
        }
      }
    }

    if (!isValidInner169Hex(innerHex)) {
      innerHex = null;
      innerVia = null;
    }
    report.inner_169_hex = innerHex;
    report.inner_via = innerVia || null;
    report.steps.push_log = pushLog.slice(-20);
    report.ok = Boolean(innerHex || create?.ok);
  } catch (e) {
    report.steps.createMessage = { error: String(e) };
  }

  try {
    sdk.removeClient?.(clientId);
  } catch {}

  if (largestPush.buf?.length) {
    const cap = join(ROOT, "analysis", "feige_push_capture.bin");
    try {
      writeFileSync(cap, largestPush.buf);
      report.steps.push_capture = { path: cap, len: largestPush.len };
    } catch (e) {
      report.steps.push_capture = { error: String(e) };
    }
  }

  if (pushRawBuffers.length) {
    const allPath = join(ROOT, "analysis", "feige_push_all.bin");
    try {
      writeFileSync(allPath, Buffer.concat(pushRawBuffers));
      report.steps.push_all = { path: allPath, frames: pushRawBuffers.length, bytes: pushRawBuffers.reduce((a, b) => a + b.length, 0) };
    } catch (e) {
      report.steps.push_all = { error: String(e) };
    }
  }

  console.log(JSON.stringify(report, null, 2));
  process.exit(report.ok ? 0 : 1);
}

main().catch((e) => {
  console.log(JSON.stringify({ ok: false, fatal: String(e) }, null, 2));
  process.exit(1);
});
