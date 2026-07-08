#!/usr/bin/env python3
"""
CDP reverse probe — capture WS.send + bdms sign stacks for offline RE.
Run while Feige is open with a buyer chat selected.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "analysis" / "reverse_probe.json"
sys.path.insert(0, str(SRC))

INSTALL_JS = r"""
() => {
  const cap = window.__reverseProbe || {
    wsStacks: [],
    xhrSamples: [],
    fetchSamples: [],
    scripts: [],
    bdms: {},
    installed: false,
  };
  window.__reverseProbe = cap;

  if (!cap.installed) {
    // --- WS send stack capture ---
    const NativeWS = WebSocket;
    const origSend = NativeWS.prototype.send;
    NativeWS.prototype.send = function(data) {
      try {
        let len = 0;
        if (data instanceof ArrayBuffer) len = data.byteLength;
        else if (data instanceof Uint8Array) len = data.length;
        else if (typeof data === "string") len = data.length;
        if (len >= 2800 && len < 4500) {
          cap.wsStacks.push({
            t: Date.now(),
            len,
            url: (this.url || "").slice(0, 200),
            stack: (new Error()).stack?.split("\n").slice(1, 25),
          });
          if (cap.wsStacks.length > 20) cap.wsStacks.shift();
        }
      } catch (e) {}
      return origSend.apply(this, arguments);
    };

    // --- XHR sign capture ---
    const origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
      this.__revUrlIn = String(url);
      this.__revMethod = method;
      return origOpen.call(this, method, url, ...rest);
    };
    const origXSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function(body) {
      const out = this.responseURL || this.__revUrlIn || "";
      if (String(this.__revUrlIn || "").includes("jinritemai.com")) {
        cap.xhrSamples.push({
          t: Date.now(),
          method: this.__revMethod,
          in: String(this.__revUrlIn).slice(0, 800),
          out: String(out).slice(0, 1200),
          hasBogus: out.includes("a_bogus="),
          stack: (new Error()).stack?.split("\n").slice(1, 15),
        });
        if (cap.xhrSamples.length > 30) cap.xhrSamples.shift();
      }
      return origXSend.apply(this, arguments);
    };

    // --- fetch ---
    const origFetch = window.fetch;
    window.fetch = function(input, init) {
      const url = typeof input === "string" ? input : input?.url;
      const p = origFetch.apply(this, arguments);
      return p.then(resp => {
        if (String(url || "").includes("jinritemai.com")) {
          cap.fetchSamples.push({
            t: Date.now(),
            in: String(url).slice(0, 800),
            out: String(resp.url || url).slice(0, 1200),
            hasBogus: String(resp.url || "").includes("a_bogus="),
          });
          if (cap.fetchSamples.length > 20) cap.fetchSamples.shift();
        }
        return resp;
      });
    };
    cap.installed = true;
  }

  // collect SDK scripts
  cap.scripts = [...document.scripts]
    .map(s => s.src || "")
    .filter(u => /bdms|secsdk|mssdk|pigeon|im\.|jinritemai|byted/i.test(u))
    .slice(0, 40);

  // bdms surface
  if (window.bdms) {
    cap.bdms = {
      keys: Object.keys(window.bdms),
      initHead: window.bdms.init?.toString?.().slice(0, 600),
      getReferer: typeof window.bdms.getReferer,
    };
  }

  // global sign hints
  cap.globals = {};
  for (const k of Object.getOwnPropertyNames(window)) {
    if (/bdms|bogus|sign|mssdk|acrawler|secsdk|byted|_0x/i.test(k)) {
      cap.globals[k] = typeof window[k];
    }
  }

  return { ok: true, wsCount: cap.wsStacks.length, xhrCount: cap.xhrSamples.length, installed: cap.installed };
}
"""

TRIGGER_ORDER_JS = r"""
async () => {
  const url = 'https://pigeon.jinritemai.com/backstage/cmpoent/order/query?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626';
  const body = {
    security_user_id: 'AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk',
    page_no: 0, page_size: 5, tab_type: 1, biz_type: 2, version: '1.0',
    workstation_opt_version: 'v2', workstation_opt_gray: true, open_params: {}, service_entity_id: '',
    search_words: '', is_init_tab: 0,
  };
  const r = await fetch(url, {
    method: 'POST', credentials: 'include',
    headers: { 'content-type': 'application/json;charset=UTF-8' },
    body: JSON.stringify(body),
  });
  let code = null;
  try { code = (await r.clone().json()).code; } catch (e) {}
  return { status: r.status, finalUrl: (r.url || url).slice(0, 1500), code };
}
"""

TRIGGER_WS_UI_JS = r"""
async () => {
  const text = '好';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const editors = [...document.querySelectorAll('[contenteditable="true"], textarea')]
    .filter(el => el.offsetParent !== null);
  const editor = editors.find(el => el.getBoundingClientRect().width > 120) || editors[0];
  if (!editor) return { ok: false, error: 'no_editor' };
  editor.focus();
  if (editor.tagName === 'TEXTAREA') { editor.value = text; editor.dispatchEvent(new Event('input', { bubbles: true })); }
  else { editor.textContent = text; editor.dispatchEvent(new InputEvent('input', { bubbles: true, data: text })); }
  await sleep(300);
  const btn = [...document.querySelectorAll('button, [role=button], span')]
    .find(el => /发送|send/i.test((el.textContent||'').trim()) && el.offsetParent);
  if (btn) btn.click();
  else editor.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true }));
  return { ok: true, text, byteLen: new TextEncoder().encode(text).length };
}
"""

DUMP_JS = "() => window.__reverseProbe || {}"


async def main(port: int = 9222) -> dict:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready

    if not cdp_ready(port):
        return {"ok": False, "error": "cdp_not_ready"}

    report: dict = {"ok": False}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        page = next(
            (pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")),
            browser.contexts[0].pages[0] if browser.contexts[0].pages else None,
        )
        if not page:
            return {"ok": False, "error": "no_page"}
        report["page"] = page.url

        install = await page.evaluate(INSTALL_JS)
        report["install"] = install

        order = await page.evaluate(TRIGGER_ORDER_JS)
        report["order_trigger"] = order
        await asyncio.sleep(0.4)

        ws = await page.evaluate(TRIGGER_WS_UI_JS)
        report["ws_trigger"] = ws
        await asyncio.sleep(1.2)

        dump = await page.evaluate(DUMP_JS)
        report["probe"] = dump
        report["ok"] = True

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    r = asyncio.run(main())
    print(json.dumps(r, ensure_ascii=False, indent=2))
