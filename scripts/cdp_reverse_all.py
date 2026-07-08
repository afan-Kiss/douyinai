#!/usr/bin/env python3
"""
CDP reverse session — harvest templates, probe bdms, capture WS sign stacks.
Output → analysis/reverse_session.json + standalone_bundle/
Runtime goal: export offline assets for PIGEON_STANDALONE=1.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

OUT = ROOT / "analysis" / "reverse_session.json"
BUNDLE = ROOT / "standalone_bundle"

WS_HOOK_JS = r"""
() => {
  if (window.__wsReverseHook) return { ok: true, already: true, n: window.__wsReverseHook.length };
  window.__wsReverseHook = [];
  const cap = window.__pigeonWsCapture;
  const ws = cap?.ws;
  if (!ws) return { ok: false, error: "no_active_ws" };
  if (!ws.__revPatched) {
    const orig = ws.send.bind(ws);
    ws.send = function(data) {
      try {
        let bytes;
        if (data instanceof ArrayBuffer) bytes = new Uint8Array(data);
        else if (data instanceof Uint8Array) bytes = data;
        else if (typeof data === "string") bytes = new TextEncoder().encode(data);
        else return orig(data);
        if (bytes.length >= 2800 && bytes.length < 4500) {
          let s = "";
          for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
          if (s.includes("s:client_message_id")) {
            window.__wsReverseHook.push({
              t: Date.now(),
              len: bytes.length,
              stack: (new Error()).stack?.split("\n").slice(1, 15),
              preview: s.match(/[\u4e00-\u9fff]{1,20}/)?.[0] || "",
            });
          }
        }
      } catch (e) {}
      return orig(data);
    };
    ws.__revPatched = true;
  }
  return { ok: true, ws: (ws.url || "").slice(0, 120), n: 0 };
}
"""

BDMS_PROBE_JS = r"""
async () => {
  const out = { globals: {}, fetchTest: null, xhrInvoke: null };
  for (const k of Object.getOwnPropertyNames(window)) {
    if (/bdms|bogus|sign|mssdk|acrawler|secsdk|byted/i.test(k))
      out.globals[k] = typeof window[k];
  }
  if (window.bdms) {
    out.bdms = {
      keys: Object.keys(window.bdms),
      initHead: window.bdms.init?.toString?.().slice(0, 400),
      referer: window.bdms.getReferer?.(),
    };
  }
  const url = "https://pigeon.jinritemai.com/backstage/cmpoent/order/query?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626";
  try {
    const r = await fetch(url, {
      method: "POST", credentials: "include",
      headers: { "content-type": "application/json;charset=UTF-8" },
      body: JSON.stringify({ security_user_id: "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk", page_no: 0, page_size: 5, tab_type: 1, biz_type: 2, version: "1.0" }),
    });
    const j = await r.json();
    out.fetchTest = { status: r.status, finalUrl: (r.url || "").slice(0, 500), code: j.code, hasBogus: (r.url || "").includes("a_bogus=") };
  } catch (e) { out.fetchTest = { error: String(e) }; }

  try {
    window.a_bogus = null;
    const xhr = new XMLHttpRequest();
    xhr.bdmsInvokeList = [{ args: ["POST", url, true] }, { args: ["content-type", "application/json;charset=UTF-8"] }];
    xhr.open("POST", url, true);
    xhr.setRequestHeader("content-type", "application/json;charset=UTF-8");
    xhr.send("{}");
    out.xhrInvoke = { a_bogus: window.a_bogus, responseURL: (xhr.responseURL || "").slice(0, 500) };
  } catch (e) { out.xhrInvoke = { error: String(e) }; }

  return out;
}
"""

SEND_UI_JS = r"""
async (text) => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const editors = [...document.querySelectorAll('[contenteditable="true"]'), ...document.querySelectorAll("textarea")].filter(el => el.offsetParent !== null);
  const editor = editors.find(el => el.getBoundingClientRect().width > 120) || editors[0];
  if (!editor) return { ok: false, error: "no_editor" };
  editor.focus();
  if (editor.tagName === "TEXTAREA") { editor.value = text; editor.dispatchEvent(new Event("input", { bubbles: true })); }
  else { editor.textContent = text; editor.dispatchEvent(new InputEvent("input", { bubbles: true })); }
  await sleep(300);
  const btn = [...document.querySelectorAll("button, [role=button], span")].find(el => /发送|send/i.test((el.textContent||"").trim()) && el.offsetParent);
  if (btn) btn.click(); else editor.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", keyCode: 13, bubbles: true }));
  return { ok: true, textLen: text.length };
}
"""


async def main() -> int:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready
    from pigeon_protocol.session_sync import CdpSessionSync
    from pigeon_protocol.session import load_session, save_session
    from pigeon_protocol.ws_template_harvest import INSTALL_CAPTURE_JS, QUICK_LADDER, missing_lengths, text_for_byte_length
    from pigeon_protocol.ws_sign_import import import_sample

    if not cdp_ready():
        print("CDP not ready — run scripts/start_feige_cdp.ps1 first", file=sys.stderr)
        return 1

    report: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "steps": []}

    # 1) session sync (async — avoid nested asyncio.run)
    session = load_session()
    sync = CdpSessionSync(session)
    report["steps"].append({"prepare": await sync._sync_async()})
    session = load_session()

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next((pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")), None)
        if not page:
            print("No Feige page — login and open buyer chat", file=sys.stderr)
            return 2
        report["page_url"] = page.url

        # 2) bdms probe
        bdms = await page.evaluate(BDMS_PROBE_JS)
        report["bdms"] = bdms
        (ROOT / "analysis" / "bdms_live_probe.json").write_text(json.dumps(bdms, ensure_ascii=False, indent=2), encoding="utf-8")

        # 3) WS hook + harvest missing template lengths
        hook = await page.evaluate(INSTALL_CAPTURE_JS)
        ws_hook = await page.evaluate(WS_HOOK_JS)
        report["ws_hook"] = {**hook, **ws_hook}

        miss = missing_lengths(QUICK_LADDER)
        harvested = []
        for byte_len in miss[:6]:  # cap 6 UI sends per session
            text = text_for_byte_length(byte_len)
            before = await page.evaluate("() => (window.__wsSignCapture?.samples?.length || 0)")
            send_r = await page.evaluate(SEND_UI_JS, text)
            if not send_r.get("ok"):
                harvested.append({"byte_len": byte_len, "error": send_r})
                continue
            await asyncio.sleep(1.5)
            after_samples = await page.evaluate("() => window.__wsSignCapture?.samples || []")
            if len(after_samples) <= before:
                harvested.append({"byte_len": byte_len, "error": "no_capture"})
                continue
            sample = after_samples[-1]
            sample["source"] = "auto_harvest"
            path = import_sample(sample)
            stable = ROOT / "captures" / "live" / "ws_sign" / f"live_ws_frame_sent_b{byte_len:03d}.json"
            if path != stable:
                stable.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            harvested.append({"byte_len": byte_len, "file": stable.name, "frame": sample.get("len")})

        report["template_harvest"] = harvested

        stacks = await page.evaluate("() => window.__wsReverseHook || []")
        report["ws_sign_stacks"] = stacks[-5:]

    # 4) export standalone bundle
    BUNDLE.mkdir(parents=True, exist_ok=True)
    import shutil

    from pigeon_protocol.config import SESSION_FILE

    shutil.copy2(SESSION_FILE, BUNDLE / "session.json")
    tpl_dir = BUNDLE / "ws_sign"
    tpl_dir.mkdir(exist_ok=True)
    for p in (ROOT / "captures" / "live" / "ws_sign").glob("live_ws_frame_sent_b*.json"):
        shutil.copy2(p, tpl_dir / p.name)

    from pigeon_protocol.capture_loader import list_send_template_pool

    report["standalone_bundle"] = str(BUNDLE)
    report["template_pool"] = list_send_template_pool()
    report["still_missing"] = missing_lengths(QUICK_LADDER)

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("bdms", {}).get("fetchTest", {}).get("code") == "0" else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
