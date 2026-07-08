/** Capture bdms browser fingerprint pipe-string from jsdom run. */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { JSDOM, ResourceLoader } from "jsdom";
import {
  installFingerprint,
  installFetchSpy,
  installLocalStorage,
  loadFingerprint,
  loadSecsdk,
} from "./bdms_fingerprint_env.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");

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
  } catch {
    /* ignore */
  }
  return "";
}

class NoNetworkLoader extends ResourceLoader {
  fetch() {
    return Promise.resolve(Buffer.from("{}"));
  }
}

const captured = [];
const origJoin = Array.prototype.join;
Array.prototype.join = function (sep, ...rest) {
  const out = origJoin.call(this, sep, ...rest);
  if (sep === "|" && out.includes("Win32") && out.length > 40) {
    captured.push(out);
  }
  return out;
};

async function main() {
  const fp = loadFingerprint();
  const cookie = loadSessionCookie() || fp?.cookie || "";
  const testUrl =
    "https://pigeon.jinritemai.com/backstage/cmpoent/order/query?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626";

  const dom = new JSDOM(`<!DOCTYPE html><html><body></body></html>`, {
    url: fp?.href || "https://im.jinritemai.com/pc_seller_v2/main/workspace",
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
  installFetchSpy(window);
  loadSecsdk(window);
  window.eval(fs.readFileSync(path.join(ROOT, "analysis", "bdms.js"), "utf8"));
  window.bdms.init({
    aid: 1383,
    pageId: 30026,
    paths: ["^/backstage/"],
    boe: false,
    ddrt: 8.5,
    ic: 8.5,
  });
  await window.fetch(testUrl, { method: "GET", credentials: "include" });

  Array.prototype.join = origJoin;
  const uniq = [...new Set(captured)].sort((a, b) => b.length - a.length);
  console.log(JSON.stringify({ count: uniq.length, fps: uniq.slice(0, 5) }, null, 2));
}

main().catch((e) => {
  console.log(JSON.stringify({ ok: false, error: String(e) }));
  process.exit(1);
});
