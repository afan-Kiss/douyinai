/** Load analysis/browser_fingerprint.json + apply to jsdom window. */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { installCanvasMocks } from "./bdms_canvas_mock.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FP_FILE = path.join(__dirname, "..", "analysis", "browser_fingerprint.json");

export function loadFingerprint() {
  if (!fs.existsSync(FP_FILE)) return null;
  try {
    return JSON.parse(fs.readFileSync(FP_FILE, "utf8"));
  } catch {
    return null;
  }
}

export function installFingerprint(window, fp = loadFingerprint()) {
  if (!fp) {
    installCanvasMocks(window);
    return { applied: false };
  }

  installCanvasMocks(window, fp);

  const nav = window.navigator;
  Object.defineProperties(nav, {
    userAgent: { get: () => fp.ua, configurable: true },
    platform: { get: () => fp.platform || "Win32", configurable: true },
    language: { get: () => fp.language || "zh-CN", configurable: true },
    languages: { get: () => fp.languages || ["zh-CN", "zh"], configurable: true },
    hardwareConcurrency: { get: () => fp.hardwareConcurrency ?? 16, configurable: true },
    deviceMemory: { get: () => fp.deviceMemory ?? 8, configurable: true },
    maxTouchPoints: { get: () => fp.maxTouchPoints ?? 0, configurable: true },
    vendor: { get: () => fp.vendor || "Google Inc.", configurable: true },
    webdriver: { get: () => false, configurable: true },
    userAgentData: {
      get: () => ({
        brands: [
          { brand: "Google Chrome", version: "149" },
          { brand: "Chromium", version: "149" },
          { brand: "Not)A;Brand", version: "24" },
        ],
        mobile: false,
        platform: "Windows",
      }),
      configurable: true,
    },
  });

  const scr = fp.screen || {};
  window.screen = {
    width: scr.w ?? 1920,
    height: scr.h ?? 1080,
    availWidth: scr.w ?? 1920,
    availHeight: (scr.h ?? 1080) - 40,
    colorDepth: scr.cd ?? 24,
    pixelDepth: scr.cd ?? 24,
  };

  const inner = fp.inner || {};
  window.innerWidth = inner.w ?? 853;
  window.innerHeight = inner.h ?? 817;
  window.outerWidth = scr.w ?? 1920;
  window.outerHeight = scr.h ?? 1080;
  window.devicePixelRatio = fp.dpr ?? 1;

  if (fp.href) {
    try {
      const u = new URL(fp.href);
      window.location.href = fp.href;
      window.location.hostname = u.hostname;
      window.location.host = u.host;
      window.location.origin = u.origin;
      window.location.pathname = u.pathname;
    } catch {
      /* jsdom location is partial */
    }
  }

  return { applied: true, s_v_web_id: fp.s_v_web_id };
}

/** Spy fetch — capture URL bdms passes to network layer (full signed query). */
export function installFetchSpy(window) {
  const captured = { requestUrl: null, responseUrl: null };
  const realFetch = globalThis.fetch.bind(globalThis);

  const spy = async (input, init) => {
    captured.requestUrl =
      typeof input === "string" ? input : input?.url || (input instanceof URL ? input.href : null);
    try {
      const resp = await realFetch(input, init);
      captured.responseUrl = resp.url || captured.requestUrl;
      return resp;
    } catch (e) {
      // bdms may hit cross-origin; signing already happened in requestUrl
      return {
        ok: true,
        status: 200,
        url: captured.requestUrl,
        text: async () => '{"code":0,"data":[]}',
        json: async () => ({ code: 0, data: [] }),
      };
    }
  };

  window.fetch = spy;
  globalThis.fetch = spy;
  window.__bdmsFetchCapture = captured;
  return captured;
}

export function installLocalStorage(window, envPath = null) {
  const fpFile = envPath || path.join(__dirname, "..", "analysis", "bdms_browser_env.json");
  if (!fs.existsSync(fpFile)) return { seeded: false };
  try {
    const env = JSON.parse(fs.readFileSync(fpFile, "utf8"));
    for (const [k, v] of Object.entries(env.localStorage || {})) {
      window.localStorage.setItem(k, v);
    }
    for (const [k, v] of Object.entries(env.sessionStorage || {})) {
      window.sessionStorage.setItem(k, v);
    }
    return { seeded: true, xmst: env.localStorage?.xmst?.slice(0, 40), csrf: env.csrfToken };
  } catch {
    return { seeded: false };
  }
}

export function loadSecsdk(window) {
  const p = path.join(__dirname, "..", "analysis", "secsdk.umd.js");
  if (!fs.existsSync(p)) return false;
  window.eval(fs.readFileSync(p, "utf8"));
  if (window.secsdk?.csrf?.setOptions) {
    window.secsdk.csrf.setOptions({
      allowList: ["jinritemai.com", "pigeon.jinritemai.com", "fxg.jinritemai.com"],
    });
  }
  return !!window.secsdk;
}

export function appendMissingTokens(url, cookie, localStorage = null) {
  try {
    const u = new URL(url);
    if (!u.searchParams.get("verifyFp")) {
      const m = cookie.match(/s_v_web_id=([^;]+)/);
      if (m) {
        u.searchParams.set("verifyFp", m[1]);
        u.searchParams.set("fp", m[1]);
      }
    }
    if (!u.searchParams.get("msToken") && localStorage?.getItem) {
      const xmst = localStorage.getItem("xmst");
      if (xmst) u.searchParams.set("msToken", xmst);
    }
    return u.toString();
  } catch {
    return url;
  }
}
export function parseSignTokens(url) {
  try {
    const u = new URL(url);
    return {
      verifyFp: u.searchParams.get("verifyFp"),
      fp: u.searchParams.get("fp"),
      msToken: u.searchParams.get("msToken"),
      a_bogus: u.searchParams.get("a_bogus"),
    };
  } catch {
    return {};
  }
}
