/**
 * Attempt to run bdms.js in Node with minimal browser mocks.
 * Usage: node scripts/run_bdms_node.mjs
 */
import fs from "fs";
import vm from "vm";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const bdmsPath = path.join(__dirname, "..", "analysis", "bdms.js");
const code = fs.readFileSync(bdmsPath, "utf8");

const logs = [];
const mockWindow = {
  bdms: undefined,
  __ac_referer: "",
  location: { href: "https://im.jinritemai.com/pc_seller_v2/main", search: "" },
  navigator: {
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    platform: "Win32",
    language: "zh-CN",
    languages: ["zh-CN", "zh"],
    webdriver: false,
  },
  document: {
    cookie: "",
    scripts: [],
    createElement: () => ({ style: {}, appendChild() {}, setAttribute() {} }),
    head: { appendChild() {} },
    body: { appendChild() {} },
    documentElement: { appendChild() {} },
  },
  localStorage: { getItem: () => null, setItem() {} },
  sessionStorage: { getItem: () => null, setItem() {} },
  XMLHttpRequest: class {
    open() {}
    setRequestHeader() {}
    send() {}
  },
  fetch: async () => ({ ok: true, status: 200, url: "", text: async () => "{}" }),
  setTimeout: (fn) => fn(),
  clearTimeout() {},
  setInterval: () => 0,
  clearInterval() {},
  console: { log: (...a) => logs.push(["log", ...a]), warn: (...a) => logs.push(["warn", ...a]) },
  performance: { now: () => Date.now(), getEntriesByType: () => [] },
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
  Uint8Array,
  ArrayBuffer,
  TextEncoder: globalThis.TextEncoder,
  TextDecoder: globalThis.TextDecoder,
  atob: (s) => Buffer.from(s, "base64").toString("binary"),
  btoa: (s) => Buffer.from(s, "binary").toString("base64"),
  crypto: globalThis.crypto,
};

mockWindow.window = mockWindow;
mockWindow.self = mockWindow;
mockWindow.globalThis = mockWindow;

const context = vm.createContext(mockWindow);

try {
  vm.runInContext(code, context, { filename: "bdms.js", timeout: 10000 });
  console.log("bdms loaded:", !!context.bdms, Object.keys(context.bdms || {}));
  if (context.bdms?.init) {
    const r = context.bdms.init({});
    console.log("init result:", r);
  }
} catch (e) {
  console.error("RUN ERROR:", e.message);
  console.error(String(e.stack).split("\n").slice(0, 8).join("\n"));
}

console.log("logs tail:", logs.slice(-5));
