/**
 * Browser env补环境 for bdms 1.0.1.20 (Feige / jinritemai).
 * Logs missing props via Proxy when BDMS_DEBUG=1.
 */
import fs from "fs";
import vm from "vm";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEBUG = process.env.BDMS_DEBUG === "1";

function createStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

const ua =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36";

const location = {
  href: "https://im.jinritemai.com/pc_seller_v2/main/workspace",
  origin: "https://im.jinritemai.com",
  protocol: "https:",
  host: "im.jinritemai.com",
  hostname: "im.jinritemai.com",
  pathname: "/pc_seller_v2/main/workspace",
  search: "",
  hash: "",
};

const document = {
  cookie: "gfkadpd=1383,30026; s_v_web_id=verify_mqxdk2fa_uGdLFVSU_xG5a_4QRO_ArB2_5O3JHoOwc8pG",
  referrer: "https://im.jinritemai.com/",
  scripts: [{ src: "https://lf-c-flwb.bytetos.com/obj/rc-client-security/web/stable/1.0.1.20/bdms.js" }],
  createElement(tag) {
    return {
      tagName: String(tag || "").toUpperCase(),
      style: {},
      appendChild() {},
      removeChild() {},
      setAttribute() {},
      getAttribute: () => null,
      addEventListener() {},
      removeEventListener() {},
    };
  },
  createTextNode(text) {
    return { nodeType: 3, textContent: String(text || "") };
  },
  createEvent() {
    return { initEvent() {} };
  },
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  head: { appendChild() {}, removeChild() {} },
  body: { appendChild() {}, removeChild() {} },
  documentElement: { appendChild() {}, removeChild() {} },
  addEventListener() {},
  removeEventListener() {},
};

const navigator = {
  userAgent: ua,
  platform: "Win32",
  language: "zh-CN",
  languages: ["zh-CN", "zh"],
  webdriver: false,
  hardwareConcurrency: 16,
  deviceMemory: 8,
  maxTouchPoints: 0,
  vendor: "Google Inc.",
  appVersion: ua.replace("Mozilla/", ""),
  plugins: { length: 0, item() { return null; }, namedItem() { return null; } },
  mimeTypes: { length: 0, item() { return null; }, namedItem() { return null; } },
  connection: { effectiveType: "4g", downlink: 10, rtt: 50 },
  userAgentData: { brands: [{ brand: "Google Chrome", version: "149" }], mobile: false, platform: "Windows" },
};

class MockXHR {
  constructor() {
    this.readyState = 0;
    this.status = 0;
    this.responseText = "";
    this.responseURL = "";
    this._headers = {};
    this.bdmsInvokeList = [];
    this.invokeList = [];
  }
  open(method, url) {
    this._method = method;
    this._url = url;
  }
  setRequestHeader(k, v) {
    this._headers[k] = v;
  }
  send(body) {
    this.readyState = 4;
    this.status = 200;
    this.responseURL = this._url;
  }
  addEventListener() {}
}

let windowObj = {
  location,
  navigator,
  document,
  localStorage: createStorage(),
  sessionStorage: createStorage(),
  XMLHttpRequest: MockXHR,
  fetch: async () => ({ ok: true, status: 200, url: "", text: async () => "{}" }),
  Request: class {},
  Headers: class {},
  URL: globalThis.URL,
  URLSearchParams: globalThis.URLSearchParams,
  setTimeout: (fn, ms = 0) => globalThis.setTimeout(fn, ms),
  clearTimeout: globalThis.clearTimeout,
  setInterval: (fn, ms) => globalThis.setInterval(fn, ms),
  clearInterval: globalThis.clearInterval,
  Date,
  Math,
  JSON,
  parseInt,
  parseFloat,
  isNaN,
  Array,
  Object,
  String,
  Number,
  Boolean,
  RegExp,
  Error,
  TypeError,
  Function,
  Symbol,
  Promise,
  Map,
  Set,
  WeakMap,
  Reflect,
  Proxy,
  MessageChannel: class {
    constructor() {
      this.port1 = { postMessage() {}, onmessage: null, start() {} };
      this.port2 = { postMessage() {}, onmessage: null, start() {} };
    }
  },
  MessageEvent: class {},
  Blob: globalThis.Blob,
  FileReader: class { readAsArrayBuffer() {} addEventListener() {} },
  DOMParser: class { parseFromString() { return { documentElement: { textContent: "" } }; } },
  Worker: class { postMessage() {} terminate() {} addEventListener() {} },
  WebSocket: class { send() {} close() {} addEventListener() {} },
  AudioContext: class { createOscillator() { return { connect() {}, start() {} }; } createGain() { return { connect() {} }; } },
  HTMLCanvasElement: class {},
  CanvasRenderingContext2D: class {},
  WebGLRenderingContext: class {},
  PluginArray: class { length = 0; item() { return null; } refresh() {} },
  MimeTypeArray: class { length = 0; item() { return null; } },
  Uint8Array,
  Uint16Array: globalThis.Uint16Array,
  Int32Array: globalThis.Int32Array,
  Float32Array: globalThis.Float32Array,
  Float64Array: globalThis.Float64Array,
  DataView: globalThis.DataView,
  ArrayBuffer,
  queueMicrotask: globalThis.queueMicrotask?.bind(globalThis) ?? ((fn) => Promise.resolve().then(fn)),
  Deno: undefined,
  RangeError: globalThis.RangeError,
  ReferenceError: globalThis.ReferenceError,
  SyntaxError: globalThis.SyntaxError,
  process: { env: {}, versions: { node: "20.0.0" }, platform: "win32" },
  TextEncoder: globalThis.TextEncoder,
  TextDecoder: globalThis.TextDecoder,
  atob: (s) => Buffer.from(s, "base64").toString("binary"),
  btoa: (s) => Buffer.from(s, "binary").toString("base64"),
  crypto: globalThis.crypto,
  performance: { now: () => Date.now(), getEntriesByType: () => [], mark() {}, measure() {} },
  console,
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent: () => true,
  Image: class {},
  MutationObserver: class { observe() {} disconnect() {} },
  HTMLElement: class {},
  Element: class {},
  Node: class {},
  Event: class {},
  CustomEvent: class {},
  getComputedStyle: () => ({}),
  screen: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040, colorDepth: 24 },
  innerWidth: 1920,
  innerHeight: 969,
  outerWidth: 1920,
  outerHeight: 1040,
  devicePixelRatio: 1,
  history: { pushState() {}, replaceState() {} },
  a_bogus: null,
};

windowObj.window = windowObj;
windowObj.self = windowObj;
windowObj.globalThis = windowObj;
windowObj.top = windowObj;
windowObj.parent = windowObj;

if (DEBUG) {
  windowObj = new Proxy(windowObj, {
    get(target, prop) {
      if (!(prop in target) && typeof prop === "string" && prop !== "Symbol") {
        console.warn("[env missing]", prop);
      }
      return target[prop];
    },
  });
}

const bdmsCode = fs.readFileSync(path.join(__dirname, "..", "analysis", "bdms.js"), "utf8");
const ctx = vm.createContext(windowObj);

try {
  vm.runInContext(bdmsCode, ctx, { filename: "bdms.js", timeout: 15000 });
} catch (e) {
  console.error("LOAD_FAIL", e.message);
  console.error(String(e.stack).split("\n").slice(0, 12).join("\n"));
  process.exit(2);
}

if (!ctx.bdms?.init) {
  console.error("NO_BDMS_INIT");
  process.exit(3);
}

const initCfg = {
  aid: 1383,
  pageId: 30026,
  paths: ["^/backstage/cmpoent/", "^/backstage/", "/cmpoent/order/query"],
  boe: false,
  ddrt: 8.5,
  ic: 8.5,
};

try {
  ctx.bdms.init(initCfg);
} catch (e) {
  console.error("INIT_FAIL", e.message);
  console.error(String(e.stack).split("\n").slice(0, 6).join("\n"));
  process.exit(4);
}

const testUrl =
  "https://pigeon.jinritemai.com/backstage/cmpoent/order/query?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626";

const xhr = new ctx.XMLHttpRequest();
xhr.bdmsInvokeList = [
  { args: ["POST", testUrl, true], func: function () {} },
  { args: ["Accept", "application/json, text/plain, */*"], func: function () {} },
  { args: ["content-type", "application/json;charset=UTF-8"], func: function () {} },
];
xhr.open("POST", testUrl, true);
xhr.setRequestHeader("content-type", "application/json;charset=UTF-8");
xhr.send("{}");

console.log(
  JSON.stringify(
    {
      a_bogus: ctx.a_bogus,
      responseURL: xhr.responseURL,
      hasBogusInUrl: String(xhr.responseURL || "").includes("a_bogus="),
    },
    null,
    2,
  ),
);
