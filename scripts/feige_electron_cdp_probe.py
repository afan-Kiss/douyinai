#!/usr/bin/env python3
"""Probe Feige Electron via --remote-debugging-port for webviewBridge / pigeon SDK."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis" / "feige_electron_cdp_probe.json"
FEIGE_EXE = Path("E:/feige-electron/抖店工作台/1.1.7/doudian.exe")
CDP_PORT = 9223

PROBE_JS = r"""
async () => {
  const out = {
    url: location.href,
    ua: navigator.userAgent,
    has_webviewBridge: !!window.webviewBridge,
    bridge_keys: window.webviewBridge ? Object.keys(window.webviewBridge) : [],
  };
  if (window.webviewBridge?.getSDKClient) {
    try {
      const client = await window.webviewBridge.getSDKClient();
      out.client_keys = client ? Object.keys(client) : [];
      out.client_methods = client ? Object.getOwnPropertyNames(Object.getPrototypeOf(client)).slice(0, 30) : [];
      for (const k of ["createClient", "invokeAsync", "invokeWithoutReturn", "initSdkFromBuffer"]) {
        out["has_" + k] = typeof client?.[k] === "function";
      }
    } catch (e) {
      out.client_error = String(e);
    }
  }
  const cap = window.__pigeonInvokeHook;
  if (cap) out.invoke_hook = { calls: (cap.calls || []).slice(-5).length, hooked: cap.hooked };
  return out;
}
"""


def find_feige_exe() -> Path:
    root = Path("E:/feige-electron")
    if FEIGE_EXE.is_file():
        return FEIGE_EXE
    for p in root.rglob("doudian.exe"):
        return p
    return FEIGE_EXE


def cdp_alive(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def launch_feige(port: int) -> subprocess.Popen | None:
    exe = find_feige_exe()
    if not exe.is_file():
        return None
    return subprocess.Popen(
        [str(exe), f"--remote-debugging-port={port}"],
        cwd=str(exe.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def probe_pages(port: int) -> dict:
    import urllib.request

    from playwright.async_api import async_playwright

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
        targets = json.loads(r.read())

    report: dict = {"port": port, "targets": [], "pages": [], "best": None}
    for t in targets:
        report["targets"].append(
            {
                "type": t.get("type"),
                "title": (t.get("title") or "")[:80],
                "url": (t.get("url") or "")[:160],
            }
        )

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        for ctx in browser.contexts:
            for page in ctx.pages:
                row = {"url": page.url[:200]}
                try:
                    row["probe"] = await page.evaluate(PROBE_JS)
                except Exception as exc:
                    row["error"] = str(exc)[:200]
                report["pages"].append(row)
                if row.get("probe", {}).get("has_webviewBridge"):
                    report["best"] = row

    report["ok"] = bool(report.get("best"))
    return report


async def main_async() -> int:
    proc = None
    launched = False
    if not cdp_alive(CDP_PORT):
        proc = launch_feige(CDP_PORT)
        launched = proc is not None
        if not launched:
            OUT.write_text(
                json.dumps({"error": f"feige exe not found: {FEIGE_EXE}"}, indent=2),
                encoding="utf-8",
            )
            return 2
        for _ in range(30):
            await asyncio.sleep(1)
            if cdp_alive(CDP_PORT):
                break
        else:
            OUT.write_text(json.dumps({"error": "cdp port timeout"}, indent=2), encoding="utf-8")
            return 2

    report = await probe_pages(CDP_PORT)
    report["launched"] = launched
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if proc and launched:
        # leave Feige running for user login / manual hook
        report["note"] = "Feige left running with CDP; login and open IM workspace then re-run probe"
    return 0 if report.get("ok") else 1


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
