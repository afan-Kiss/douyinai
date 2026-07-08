/**
 * Load sdk-glue + bdms with real network — probe byted_acrawler.frontierSign.
 * stdin: JSON { "X-MS-STUB": "md5hex" }  stdout: { ok, headers, probe }
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { JSDOM, ResourceLoader } from "jsdom";
import {
  installFingerprint,
  installLocalStorage,
  loadFingerprint,
  loadSecsdk,
} from "./bdms_fingerprint_env.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");
const SECSDK_LOCAL = path.join(ROOT, "analysis", "secsdk.umd.js");

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
      return Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join("; ");
    }
  } catch {}
  return "";
}

class NetworkLoader extends ResourceLoader {
  fetch(url) {
    return super.fetch(url).catch(() => Promise.resolve(Buffer.from("")));
  }
}

const stubRaw = fs.readFileSync(0, "utf8").trim();
const stubIn = stubRaw ? JSON.parse(stubRaw) : { "X-MS-STUB": "d41d8cd98f00b204e9800998ecf8427e" };

async function main() {
  const fp = loadFingerprint();
  const cookie = loadSessionCookie() || fp?.cookie || "";

  const dom = new JSDOM(
    `<!DOCTYPE html><html><head>
      <script src="https://lf-c-flwb.bytetos.com/obj/rc-client-security/web/glue/1.0.0.65/sdk-glue.js"></script>
      <script src="https://lf-c-flwb.bytetos.com/obj/rc-client-security/web/stable/1.0.1.20/bdms.js"></script>
    </head><body></body></html>`,
    {
      url: fp?.href || "https://im.jinritemai.com/pc_seller_v2/main/workspace",
      referrer: "https://im.jinritemai.com/",
      pretendToBeVisual: true,
      runScripts: "dangerously",
      resources: new NetworkLoader(),
    },
  );

  const { window } = dom;
  window.Headers = globalThis.Headers;
  window.Request = globalThis.Request;
  window.document.cookie = cookie;
  installFingerprint(window, fp);
  installLocalStorage(window);
  loadSecsdk(window);
  if (fs.existsSync(SECSDK_LOCAL)) {
    const sec = window.document.createElement("script");
    sec.textContent = fs.readFileSync(SECSDK_LOCAL, "utf8");
    window.document.head.appendChild(sec);
  }

  // Feige IM aid/pageId
  try {
    if (window.bdms?.init) {
      window.bdms.init({
        aid: 1383,
        pageId: 30026,
        paths: { include: ["/pigeon_im/", "/backstage/"] },
        boe: false,
      });
    }
  } catch (e) {
    /* non-fatal */
  }

  await new Promise((r) => setTimeout(r, 3000));

  // Poll for acrawler — glue may inject asynchronously after bdms.init
  for (let i = 0; i < 20 && !window.byted_acrawler; i++) {
    await new Promise((r) => setTimeout(r, 500));
  }

  const probe = {
    bdms: !!window.bdms,
    glue: !!window._SdkGlueInit || !!window._sdkGlueVersionMap,
    acrawler: !!window.byted_acrawler,
    acrawlerKeys: window.byted_acrawler ? Object.keys(window.byted_acrawler).slice(0, 20) : [],
    webmssdk: !!window.webmssdk,
    hasFrontier: typeof window.byted_acrawler?.frontierSign === "function",
  };

  let headers = {};
  let ok = false;
  if (probe.hasFrontier) {
    try {
      headers = window.byted_acrawler.frontierSign(stubIn) || {};
      ok = Object.keys(headers).length > 0;
    } catch (e) {
      probe.frontierError = String(e);
    }
  }

  process.stdout.write(JSON.stringify({ ok, headers, probe }, null, 2));
  process.exit(ok ? 0 : 1);
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, error: String(e), headers: {} }));
  process.exit(2);
});
