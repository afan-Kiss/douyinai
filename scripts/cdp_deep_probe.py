#!/usr/bin/env python3
"""Deep CDP probe: inspect bdms SDK, hook fetch/XHR, capture signed pigeon URLs."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

DEEP_PROBE = r"""
() => {
  const report = { frames: [], bdms: null, scripts: [], xhrHook: "pending" };

  const inspectBdms = (win, label) => {
    const b = win.bdms;
    if (!b) return null;
    const info = { label, type: typeof b, keys: Object.keys(b), fns: {} };
    for (const k of Object.keys(b)) {
      const v = b[k];
      info.fns[k] = typeof v;
      if (typeof v === "function") {
        info.fns[k] = "function:" + v.toString().slice(0, 300);
      }
    }
    return info;
  };

  report.bdms = inspectBdms(window, "top");
  for (const fr of [...document.querySelectorAll("iframe")]) {
    try {
      const w = fr.contentWindow;
      report.frames.push({ src: fr.src || "", bdms: inspectBdms(w, fr.src || "iframe") });
    } catch (e) {
      report.frames.push({ src: fr.src || "", error: String(e) });
    }
  }

  // search inline script hints
  const hints = [];
  for (const s of document.scripts) {
    const t = s.src || s.textContent || "";
    if (/a_bogus|bdms|mssdk|acrawler|secsdk|sign\(/i.test(t)) {
      hints.push((s.src || t.slice(0, 120)).slice(0, 200));
    }
  }
  report.scripts = hints.slice(0, 30);

  // install hooks once
  if (!window.__pigeonSignHook) {
    window.__pigeonSignHook = { urls: [] };
    const push = (u) => {
      if (typeof u === "string" && (u.includes("a_bogus=") || u.includes("pigeon.jinritemai.com"))) {
        window.__pigeonSignHook.urls.push({ t: Date.now(), url: u.slice(0, 2000) });
      }
    };
    const origFetch = window.fetch;
    window.fetch = async function(...args) {
      push(typeof args[0] === "string" ? args[0] : args[0]?.url);
      return origFetch.apply(this, args);
    };
    const origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
      push(String(url));
      return origOpen.call(this, method, url, ...rest);
    };
    report.xhrHook = "installed";
  } else {
    report.xhrHook = "already";
    report.captured = window.__pigeonSignHook.urls.slice(-10);
  }

  // probe bdms.init if present
  const attempts = [];
  if (window.bdms?.init) {
    try {
      const r = window.bdms.init({});
      attempts.push({ fn: "bdms.init", ok: true, result: String(r).slice(0, 200) });
    } catch (e) {
      attempts.push({ fn: "bdms.init", ok: false, error: String(e).slice(0, 200) });
    }
  }
  if (window.bdms?.getReferer) {
    try {
      const r = window.bdms.getReferer();
      attempts.push({ fn: "bdms.getReferer", ok: true, result: String(r).slice(0, 200) });
    } catch (e) {
      attempts.push({ fn: "bdms.getReferer", ok: false, error: String(e).slice(0, 200) });
    }
  }
  report.attempts = attempts;
  return report;
}
"""

CLICK_CONV = r"""
() => {
  const items = [...document.querySelectorAll('[class*="conversation"],[class*="session"],[class*="chat-item"],li,div')];
  for (const el of items) {
    const t = (el.textContent || '').trim();
    if (t.length > 2 && t.length < 40 && !/设置|搜索|全部|待回复|已回复|系统/.test(t)) {
      el.click();
      return { clicked: t.slice(0, 40) };
    }
  }
  return { clicked: null };
}
"""


async def run(port: int, wait: float) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    out: dict[str, Any] = {"ok": False}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0]
        pages = [pg for pg in ctx.pages if pg.url and "about:blank" not in pg.url]
        page = pages[0] if pages else ctx.pages[0]
        out["page"] = page.url

        # install hook on all frames
        for fr in page.frames:
            try:
                await fr.evaluate(DEEP_PROBE)
            except Exception as exc:
                out.setdefault("frame_errors", []).append(str(exc)[:120])

        click = await page.evaluate(CLICK_CONV)
        out["click"] = click
        await asyncio.sleep(wait)

        probe = await page.evaluate(DEEP_PROBE)
        out["probe"] = probe

        # CDP-level network capture
        captured: list[str] = []

        def on_req(req):
            u = req.url or ""
            if "pigeon.jinritemai.com" in u and ("a_bogus=" in u or "order/query" in u):
                captured.append(u)

        page.on("request", on_req)
        await asyncio.sleep(2)
        out["network_captured"] = captured[-10:]

        # enumerate performance entries
        perf = await page.evaluate(
            """() => performance.getEntriesByType('resource')
              .filter(e => e.name.includes('pigeon.jinritemai.com'))
              .slice(-15)
              .map(e => e.name.slice(0, 500))"""
        )
        out["perf_pigeon"] = perf
        out["ok"] = True
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--wait", type=float, default=6)
    args = ap.parse_args()
    result = asyncio.run(run(args.port, args.wait))
    path = ROOT / "logs" / "cdp_deep_probe.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
