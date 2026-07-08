#!/usr/bin/env python3
"""Install WS send hook, wait for captures, import into template pool."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

PATCH = ROOT / "scripts" / "patch_active_ws_capture.py"


async def main() -> int:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready
    from pigeon_protocol.ws_sign_import import import_sample

    wait_sec = int(sys.argv[1]) if len(sys.argv) > 1 else 45
    if not cdp_ready():
        print("CDP not ready — start Feige Chrome on 9222", file=sys.stderr)
        return 1

    # reuse patch JS from patch_active_ws_capture
    patch_src = (ROOT / "scripts" / "patch_active_ws_capture.py").read_text(encoding="utf-8")
    start = patch_src.index('PATCH_ACTIVE_WS = r"""') + len('PATCH_ACTIVE_WS = r"""')
    end = patch_src.index('"""', start)
    patch_js = patch_src[start:end]
    poll_js = "() => ({ n: window.__wsSignCapture?.samples?.length || 0, samples: window.__wsSignCapture?.samples || [] })"

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next((pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")), None)
        if not page:
            print("No Feige page", file=sys.stderr)
            return 1
        status = await page.evaluate(f"() => {{ const fn = {patch_js}; return fn(); }}")
        print(json.dumps(status, ensure_ascii=False))
        print(f"请在飞鸽发送消息（含长文本），等待 {wait_sec}s ...")
        t0 = time.time()
        last = 0
        while time.time() - t0 < wait_sec:
            await asyncio.sleep(0.5)
            n = await page.evaluate("() => window.__wsSignCapture?.samples?.length || 0")
            if n != last:
                last = n
                print(f"  captured {n} sample(s)")
        result = await page.evaluate(poll_js)
        samples = result.get("samples") or []

    saved = []
    for s in samples:
        try:
            path = import_sample(s)
            saved.append(str(path.name))
        except Exception as exc:
            print(f"import fail: {exc}", file=sys.stderr)

    out = ROOT / "analysis" / "ws_sign_samples.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(samples)} raw -> {out}")
    print(f"Imported {len(saved)} -> captures/live/ws_sign/: {saved}")

    if saved:
        import subprocess

        subprocess.run([sys.executable, str(ROOT / "scripts" / "scan_ws_sent_pool.py")], check=False)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "analyze_ws_blob_delta.py")], check=False)
    return 0 if saved else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
