#!/usr/bin/env python3
"""Patch active Feige WebSocket.send and capture outbound text frames."""
from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "analysis" / "ws_sign_samples.json"
sys.path.insert(0, str(SRC))

PATCH_ACTIVE_WS = r"""
() => {
  if (!window.__wsSignCapture) window.__wsSignCapture = { samples: [], patched: false };
  const cap = window.__pigeonWsCapture;
  const ws = cap?.ws;
  if (!ws) return { ok: false, error: 'no_active_ws', cap: !!cap };
  if (ws.__signPatched) return { ok: true, already: true, n: window.__wsSignCapture.samples.length };
  const orig = ws.send.bind(ws);
  ws.send = function(data) {
    try {
      let bytes;
      if (data instanceof ArrayBuffer) bytes = new Uint8Array(data);
      else if (data instanceof Uint8Array) bytes = data;
      else if (typeof data === 'string') bytes = new TextEncoder().encode(data);
      else return orig(data);
      if (bytes.length >= 2800 && bytes.length < 4000) {
        let s = '';
        for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
        if (s.includes('s:client_message_id')) {
          window.__wsSignCapture.samples.push({
            t: Date.now(), len: bytes.length, b64: btoa(s),
            url: (ws.url || '').slice(0, 200),
          });
        }
      }
    } catch (e) {}
    return orig(data);
  };
  ws.__signPatched = true;
  window.__wsSignCapture.patched = true;
  return { ok: true, already: false, n: window.__wsSignCapture.samples.length, url: (ws.url||'').slice(0,120) };
}
"""

POLL = "() => ({ n: window.__wsSignCapture?.samples?.length || 0, samples: window.__wsSignCapture?.samples || [] })"


async def main() -> int:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready

    wait_sec = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    if not cdp_ready():
        print("CDP not ready", file=sys.stderr)
        return 1

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next((pg for pg in browser.contexts[0].pages if "jinritemai.com" in (pg.url or "")), None)
        if not page:
            print("No Feige page", file=sys.stderr)
            return 1

        status = await page.evaluate(PATCH_ACTIVE_WS)
        print(json.dumps(status, ensure_ascii=False))

        print(f"请在飞鸽再发 1-2 条同长度消息（如「好的」「收到」），等待 {wait_sec}s ...")
        t0 = time.time()
        last = 0
        while time.time() - t0 < wait_sec:
            await asyncio.sleep(0.5)
            n = await page.evaluate("() => window.__wsSignCapture?.samples?.length || 0")
            if n != last:
                last = n
                print(f"  got {n} sample(s)")

        result = await page.evaluate(POLL)
        samples = result.get("samples") or []
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")

        from pigeon_protocol.ws_sign import extract_client_message_id, locate_signature_region

        for i, s in enumerate(samples):
            raw = base64.b64decode(s["b64"])
            texts = re.findall(r"[\u4e00-\u9fff]{1,20}", raw.decode("utf-8", errors="ignore"))
            region = locate_signature_region(raw)
            print(f"[{i}] len={len(raw)} text={texts[:2]} cid={extract_client_message_id(raw)[:8]}...")
            if region:
                print(f"     blob={region.blob[:32].decode('ascii', errors='replace')}...")

        print(f"Saved {len(samples)} -> {OUT}")
        return 0 if samples else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
