#!/usr/bin/env python3
"""CDP: browser UI send + frontierSign / WS / inner blob capture for Rust SDK RE."""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "analysis" / "pigeon_rust_hook.json"
sys.path.insert(0, str(SRC))

HOOK_JS = r"""
() => {
  if (window.__pigeonRustHook?.installed) return { already: true };
  const cap = {
    installed: true,
    frontierCalls: [],
    wsSends: [],
    invokeSnaps: [],
  };
  window.__pigeonRustHook = cap;

  const b64Head = (data) => {
    try {
      let u8;
      if (data instanceof ArrayBuffer) u8 = new Uint8Array(data);
      else if (data instanceof Uint8Array) u8 = data;
      else if (typeof data === "string") u8 = new TextEncoder().encode(data);
      else return { len: 0, head_hex: "", has_cid: false };
      let s = "";
      for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
      return {
        len: u8.length,
        head_hex: Array.from(u8.slice(0, 48)).map(b => b.toString(16).padStart(2, "0")).join(""),
        has_cid: s.includes("s:client_message_id"),
        b64: (u8.length >= 2800 && u8.length < 4500 && s.includes("s:client_message_id"))
          ? btoa(s) : "",
      };
    } catch (e) { return { len: 0, error: String(e) }; }
  };

  const origSend = WebSocket.prototype.send;
  WebSocket.prototype.send = function(data) {
    try {
      const info = b64Head(data);
      if (info.len >= 2800 && info.len < 4500) {
        cap.wsSends.push({ t: Date.now(), url: (this.url || "").slice(0, 160), ...info });
      }
    } catch (e) {}
    return origSend.apply(this, arguments);
  };

  const wrapFrontier = () => {
    const ac = window.byted_acrawler;
    if (!ac || ac.__pigeonFrontierHooked) return !!ac?.frontierSign;
    if (typeof ac.frontierSign !== "function") return false;
    const orig = ac.frontierSign.bind(ac);
    ac.frontierSign = function(stub) {
      let out = {};
      try { out = orig(stub) || {}; } catch (e) { out = { error: String(e) }; }
      cap.frontierCalls.push({
        t: Date.now(),
        stub,
        out,
        stack: (new Error()).stack?.split("\n").slice(1, 12),
      });
      return out;
    };
    ac.__pigeonFrontierHooked = true;
    return true;
  };

  wrapFrontier();
  cap._frontierTimer = setInterval(wrapFrontier, 800);

  // webviewBridge invoke hook (Feige Electron — cmd 11327 PigeonIMCreateMessage)
  const tryInvokeHook = () => {
    const bridge = window.webviewBridge;
    if (!bridge || bridge.__pigeonHooked) return !!bridge;
    for (const name of ["invokeWithoutReturn", "invokeAsync", "invoke"]) {
      const orig = bridge[name];
      if (typeof orig !== "function") continue;
      bridge[name] = function(...args) {
        const row = { t: Date.now(), fn: name, args_len: args?.length || 0 };
        try {
          const out = orig.apply(this, args);
          if (out && typeof out.then === "function") {
            return out.then((res) => {
              try { row.result = JSON.stringify(res).slice(0, 2000); } catch (e) {}
              cap.invokeSnaps.push(row);
              return res;
            });
          }
          try { row.result = JSON.stringify(out).slice(0, 2000); } catch (e) {}
          cap.invokeSnaps.push(row);
          return out;
        } catch (e) {
          row.error = String(e);
          cap.invokeSnaps.push(row);
          throw e;
        }
      };
    }
    bridge.__pigeonHooked = true;
    return true;
  };
  tryInvokeHook();
  cap._invokeTimer = setInterval(tryInvokeHook, 500);

  // Probe pigeon IM globals
  cap.globals = [];
  for (const k of ["byted_acrawler", "bdms", "webviewBridge", "__PIEON_IM__"]) {
    try {
      const v = window[k];
      if (v != null) cap.globals.push({ k, type: typeof v, keys: typeof v === "object" ? Object.keys(v).slice(0, 12) : [] });
    } catch (e) {}
  }

  return { installed: true, frontierSign: wrapFrontier() };
}
"""

UNHOOK_JS = r"""
() => {
  if (window.__pigeonRustHook?._frontierTimer) clearInterval(window.__pigeonRustHook._frontierTimer);
  if (window.__pigeonRustHook?._invokeTimer) clearInterval(window.__pigeonRustHook._invokeTimer);
}
"""


def _decode_inner_from_b64(b64: str) -> dict:
    from pigeon_protocol.ws_sign import locate_signature_region
    from pigeon_protocol.ws_sign_decode import decode_blob, guess_inner_layout

    try:
        raw = base64.b64decode(b64)
        region = locate_signature_region(raw)
        if not region:
            return {"error": "no_sig_region"}
        inner = decode_blob(region.blob)
        return {
            "frame_len": len(raw),
            "inner_hex": inner.hex(),
            "layout": guess_inner_layout(inner),
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


async def main() -> int:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready
    from pigeon_protocol.ws_template_harvest import (
        INSTALL_CAPTURE_JS,
        POLL_SAMPLES_JS,
        _ensure_chat_open,
        _ensure_ws_connected,
        _send_via_ui,
    )

    if not cdp_ready():
        OUT.write_text(json.dumps({"error": "CDP not ready"}, indent=2), encoding="utf-8")
        print("CDP not ready", file=sys.stderr)
        return 2

    report: dict = {}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(
            (pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")),
            browser.contexts[0].pages[0],
        )
        report["page"] = page.url[:200]
        report["chat"] = await _ensure_chat_open(page)
        report["ws"] = await _ensure_ws_connected(page)
        report["hook"] = await page.evaluate(HOOK_JS)
        report["capture_hook"] = await page.evaluate(INSTALL_CAPTURE_JS)

        before_ws = len((await page.evaluate("() => window.__pigeonRustHook?.wsSends || []")) or [])
        before_cap = len((await page.evaluate(POLL_SAMPLES_JS)) or [])

        report["send"] = await _send_via_ui(page, "好")
        await asyncio.sleep(2.5)

        rust_hook = await page.evaluate("() => window.__pigeonRustHook || {}")
        samples = await page.evaluate(POLL_SAMPLES_JS) or []
        report["ws_sends"] = (rust_hook.get("wsSends") or [])[before_ws:]
        report["frontier_calls"] = rust_hook.get("frontierCalls") or []
        report["invoke_snaps"] = rust_hook.get("invokeSnaps") or []
        report["globals"] = rust_hook.get("globals") or []
        report["new_samples"] = samples[before_cap:] if isinstance(samples, list) else []

        inners: list[dict] = []
        for row in report["ws_sends"]:
            if row.get("b64"):
                inners.append(_decode_inner_from_b64(row["b64"]))
        for row in report.get("new_samples") or []:
            b64 = row.get("b64") if isinstance(row, dict) else None
            if b64:
                inners.append(_decode_inner_from_b64(b64))
        report["inners"] = inners

        # frontierSign offline probe
        report["frontier_probe"] = await page.evaluate(
            """() => {
              if (!window.byted_acrawler?.frontierSign) return { available: false };
              try {
                const out = window.byted_acrawler.frontierSign({
                  "X-MS-STUB": "d41d8cd98f00b204e9800998ecf8427e"
                });
                return { available: true, out };
              } catch (e) { return { available: true, error: String(e) }; }
            }"""
        )

        await page.evaluate(UNHOOK_JS)

    report["ok"] = bool(report.get("ws_sends") or report.get("new_samples") or report.get("frontier_calls"))
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(
        {
            "ok": report["ok"],
            "ws_sends": len(report.get("ws_sends") or []),
            "frontier_calls": len(report.get("frontier_calls") or []),
            "inners": len(report.get("inners") or []),
            "frontier_probe": report.get("frontier_probe"),
            "out": str(OUT),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
