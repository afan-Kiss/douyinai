/**
 * bdms 1.0.1.20 offline sign — jsdom + browser fingerprint + fetch spy.
 */
import fs from "fs";
import path from "path";
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

class NoNetworkLoader extends ResourceLoader {
  fetch() {
    return Promise.resolve(Buffer.from('{"code":0,"data":[]}'));
  }
}

const testUrl =
  process.argv[2] ||
  "https://pigeon.jinritemai.com/backstage/cmpoent/order/query?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626";

const bodyStr = process.argv[3] ?? "";
const method = (process.argv[4] || "POST").toUpperCase();

async function main() {
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

  const fpInfo = installFingerprint(window, fp);
  const storageInfo = installLocalStorage(window);
  const capture = installFetchSpy(window);

  const _error = console.error;
  console.error = (...args) => {
    const msg = String(args[0] || "");
    if (msg.includes("Not implemented: HTMLCanvasElement")) return;
    if (msg.includes("Cross origin")) return;
    _error(...args);
  };

  const hasSecsdk = loadSecsdk(window);

  const bdmsCode = fs.readFileSync(path.join(ROOT, "analysis", "bdms.js"), "utf8");
  window.eval(bdmsCode);

  if (!window.bdms?.init) {
    console.log(JSON.stringify({ ok: false, stage: "load" }));
    process.exit(3);
  }

  window.bdms.init({
    aid: 1383,
    pageId: 30026,
    paths: ["^/backstage/cmpoent/", "^/backstage/", "/cmpoent/order/query"],
    boe: false,
    ddrt: 8.5,
    ic: 8.5,
  });

  let status = 0;
  try {
    const fetchOpts = {
      method,
      credentials: "include",
      headers: {},
    };
    if (method === "POST") {
      fetchOpts.headers["content-type"] = "application/json;charset=UTF-8";
      fetchOpts.body =
        bodyStr ||
        JSON.stringify({
          security_user_id:
            "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk",
          page_no: 0,
          page_size: 5,
          tab_type: 1,
          biz_type: 2,
          version: "1.0",
          workstation_opt_version: "v2",
          workstation_opt_gray: true,
          open_params: {},
          service_entity_id: "",
          search_words: "",
          is_init_tab: 0,
        });
    }
    const resp = await window.fetch(testUrl, fetchOpts);
    status = resp.status;
    capture.responseUrl = resp.url || capture.responseUrl;
  } catch (e) {
    console.log(JSON.stringify({ ok: false, stage: "fetch", error: String(e), capture }));
    process.exit(5);
  }

  let signedUrl = appendMissingTokens(
    capture.requestUrl || capture.responseUrl || testUrl,
    cookie,
    window.localStorage,
  );
  const tokens = parseSignTokens(signedUrl);

  console.log(
    JSON.stringify(
      {
        ok: !!(tokens.a_bogus && tokens.msToken && tokens.verifyFp),
        partial: !!tokens.a_bogus,
        ...tokens,
        signedUrl: signedUrl.slice(0, 800),
        status,
        fingerprint: fpInfo,
        storage: storageInfo,
        secsdk: hasSecsdk,
        capture: {
          requestUrl: (capture.requestUrl || "").slice(0, 800),
          responseUrl: (capture.responseUrl || "").slice(0, 800),
        },
      },
      null,
      2,
    ),
  );
  console.error = _error;
  process.exit(tokens.a_bogus && tokens.msToken ? 0 : tokens.a_bogus ? 7 : 6);
}

main().catch((e) => {
  console.log(JSON.stringify({ ok: false, stage: "main", error: String(e) }));
  process.exit(1);
});
