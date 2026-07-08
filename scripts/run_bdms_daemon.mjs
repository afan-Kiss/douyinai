/**
 * Persistent bdms sign daemon — JSON lines on stdin/stdout.
 * Request:  {"id":1,"url":"...","body":"","method":"GET"}
 * Response: {"id":1,"ok":true,"a_bogus":"...","msToken":"...","signedUrl":"..."}
 */
import fs from "fs";
import path from "path";
import readline from "readline";
import { fileURLToPath } from "url";
import { JSDOM, ResourceLoader } from "jsdom";
import {
  appendMissingTokens,
  installFingerprint,
  installFetchSpy,
  installLocalStorage,
  loadFingerprint,
  loadSecsdk,
  parseSignTokens,
} from "./bdms_fingerprint_env.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");

class NoNetworkLoader extends ResourceLoader {
  fetch() {
    return Promise.resolve(Buffer.from('{"code":0,"data":[]}'));
  }
}

function loadSessionCookie() {
  const p = path.join(ROOT, "session", "session.json");
  if (!fs.existsSync(p)) return "";
  try {
    const s = JSON.parse(fs.readFileSync(p, "utf8"));
    const cookies = s.cookies;
    if (Array.isArray(cookies)) {
      return cookies.filter((c) => c.name && c.value).map((c) => `${c.name}=${c.value}`).join("; ");
    }
    if (cookies && typeof cookies === "object") {
      return Object.entries(cookies)
        .filter(([k, v]) => k && v != null && String(v))
        .map(([k, v]) => `${k}=${v}`)
        .join("; ");
    }
    return "";
  } catch {
    return "";
  }
}

let ctx = null;

function ensureContext() {
  if (ctx) return ctx;
  const fp = loadFingerprint();
  const cookie = loadSessionCookie() || fp?.cookie || "";
  const dom = new JSDOM(`<!DOCTYPE html><html><head></head><body></body></html>`, {
    url: fp?.href || "https://im.jinritemai.com/pc_seller_v2/main/workspace",
    referrer: "https://im.jinritemai.com/",
    pretendToBeVisual: true,
    runScripts: "dangerously",
    resources: new NoNetworkLoader(),
  });
  const { window } = dom;
  window.Headers = globalThis.Headers;
  window.Request = globalThis.Request;
  window.document.cookie = cookie;
  installFingerprint(window, fp);
  installLocalStorage(window);
  loadSecsdk(window);
  const capture = installFetchSpy(window);
  const _error = console.error;
  console.error = (...args) => {
    const msg = String(args[0] || "");
    if (msg.includes("Not implemented: HTMLCanvasElement")) return;
    if (msg.includes("Cross origin")) return;
    _error(...args);
  };
  window.eval(fs.readFileSync(path.join(ROOT, "analysis", "bdms.js"), "utf8"));
  if (!window.bdms?.init) throw new Error("bdms.init missing");
  window.bdms.init({
    aid: 1383,
    pageId: 30026,
    paths: ["^/backstage/cmpoent/", "^/backstage/", "/cmpoent/order/query"],
    boe: false,
    ddrt: 8.5,
    ic: 8.5,
  });
  ctx = { window, cookie, dom, capture };
  return ctx;
}

async function signOne({ url, body = "", method = "GET" }) {
  const { window, cookie, capture } = ensureContext();
  window.document.cookie = loadSessionCookie() || cookie;
  capture.requestUrl = null;
  capture.responseUrl = null;
  const fetchOpts = { method: method.toUpperCase(), credentials: "include", headers: {} };
  if (method.toUpperCase() === "POST") {
    fetchOpts.headers["content-type"] = "application/json;charset=UTF-8";
    fetchOpts.body = body || "{}";
  }
  try {
    await window.fetch(url, fetchOpts);
  } catch {
    /* signed in capture.requestUrl */
  }
  const signedUrl = appendMissingTokens(
    capture.requestUrl || capture.responseUrl || url,
    window.document.cookie,
    window.localStorage,
  );
  const tokens = parseSignTokens(signedUrl);
  return {
    ok: !!(tokens.a_bogus && tokens.msToken),
    partial: !!tokens.a_bogus,
    ...tokens,
    signedUrl: signedUrl.slice(0, 2000),
    capture: {
      requestUrl: (capture.requestUrl || "").slice(0, 2000),
    },
  };
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", async (line) => {
  if (!line.trim()) return;
  let req;
  try {
    req = JSON.parse(line);
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: "bad json" }) + "\n");
    return;
  }
  const id = req.id ?? null;
  try {
    const out = await signOne(req);
    process.stdout.write(JSON.stringify({ id, ...out }) + "\n");
  } catch (e) {
    process.stdout.write(JSON.stringify({ id, ok: false, error: String(e) }) + "\n");
  }
});

process.stderr.write("[bdms-daemon] ready\n");
