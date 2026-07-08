#!/usr/bin/env python3
"""Probe Feige page DOM for chat editor (main + iframes)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

PROBE_JS = r"""
() => {
  const scan = (doc, label) => {
    const out = { label, editors: [], buttons: [], inputs: [] };
    if (!doc) return out;
    for (const el of doc.querySelectorAll('[contenteditable="true"], textarea, input')) {
      const r = el.getBoundingClientRect();
      if (r.width < 20) continue;
      out.editors.push({
        tag: el.tagName,
        ce: el.getAttribute("contenteditable"),
        w: Math.round(r.width),
        h: Math.round(r.height),
        ph: el.placeholder || el.getAttribute("placeholder") || "",
        cls: (el.className || "").toString().slice(0, 80),
      });
    }
    for (const el of doc.querySelectorAll('button, [role=button]')) {
      const t = (el.textContent || "").trim();
      if (/发送|send/i.test(t)) out.buttons.push(t.slice(0, 20));
    }
    for (const el of doc.querySelectorAll('input[placeholder*="搜"]')) {
      out.inputs.push(el.placeholder || "");
    }
    return out;
  };
  const report = { main: scan(document, "main"), frames: [] };
  for (const fr of document.querySelectorAll("iframe")) {
    try {
      report.frames.push({ src: (fr.src || "").slice(0, 120), ...scan(fr.contentDocument, "iframe") });
    } catch (e) {
      report.frames.push({ src: (fr.src || "").slice(0, 120), error: String(e) });
    }
  }
  report.url = location.href;
  return report;
}
"""


async def main(port: int = 9222) -> dict:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        page = next((pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")), None)
        if not page:
            return {"error": "no_page"}
        report = {"page": await page.evaluate(PROBE_JS)}
        for fr in page.frames:
            if fr == page.main_frame:
                continue
            try:
                report.setdefault("pw_frames", []).append({
                    "url": fr.url[:120],
                    "probe": await fr.evaluate(PROBE_JS),
                })
            except Exception as exc:
                report.setdefault("pw_frame_errors", []).append(str(exc)[:120])
        return report


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), ensure_ascii=False, indent=2))
