#!/usr/bin/env python3
"""Capture Feige WS text-send frames via Playwright websocket events (no reload needed)."""
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
CAP_DIR = ROOT / "captures" / "live" / "ws_sign"
sys.path.insert(0, str(SRC))


def _summarize(samples: list[dict]) -> None:
    from pigeon_protocol.ws_sign import extract_client_message_id, locate_signature_region

    for i, s in enumerate(samples):
        raw = base64.b64decode(s["b64"])
        texts = re.findall(r"[\u4e00-\u9fff]{1,30}", raw.decode("utf-8", errors="ignore"))
        region = locate_signature_region(raw)
        cid = extract_client_message_id(raw)
        blob_pre = region.blob[:24].decode("ascii", errors="replace") if region else ""
        print(f"  [{i}] len={len(raw)} text={texts[:2]} cid={cid[:8]}... blob={blob_pre}...")


def save_samples(samples: list[dict]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
    CAP_DIR.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(samples):
        raw = base64.b64decode(s["b64"])
        texts = re.findall(r"[\u4e00-\u9fff]{1,30}", raw.decode("utf-8", errors="ignore"))
        text_byte_len = len(texts[0].encode("utf-8")) if texts else 0
        label = texts[0] if texts else f"len{text_byte_len or s.get('len', len(raw))}"
        safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", label)[:24] or f"sample{i:03d}"
        ev = {
            "type": "ws_frame_sent",
            "source": "playwright_ws_event",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(s.get("t", 0) / 1000)),
            "url": s.get("url", ""),
            "payload": s["b64"],
            "payload_length": s.get("len", len(raw)),
            "text_hint": texts[:3],
            "text_byte_length": text_byte_len,
        }
        out_path = CAP_DIR / f"live_ws_frame_sent_{safe}.json"
        out_path.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  saved {out_path.name} text_byte_len={text_byte_len}")


async def main() -> int:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready
    from pigeon_protocol.cdp_ws import _WS_HOOK_INSTALL_JS, _WS_INIT_SCRIPT
    from pigeon_protocol.config import FEIGE_URL

    wait_sec = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    do_prepare = "--no-prepare" not in sys.argv

    if not cdp_ready():
        print("CDP not ready", file=sys.stderr)
        return 1

    samples: list[dict] = []

    def on_frame(payload: str | bytes) -> None:
        if isinstance(payload, str):
            raw = payload.encode("latin-1", errors="ignore")
        else:
            raw = bytes(payload)
        if not (2800 <= len(raw) < 4000):
            return
        text = raw.decode("utf-8", errors="ignore")
        # text-message send frames are ~3k and contain s:client_message_id + signature region
        if "s:client_message_id" not in text:
            return
        if "request_log" in text and "feat/" in text and "type" not in text:
            return
        samples.append({"t": time.time() * 1000, "len": len(raw), "b64": base64.b64encode(raw).decode(), "url": ""})
        texts = re.findall(r"[\u4e00-\u9fff]{1,20}", text)
        print(f"  captured frame len={len(raw)} text={texts[:2]}")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        await ctx.add_init_script(_WS_INIT_SCRIPT)

        page = None
        for pg in ctx.pages:
            if "jinritemai.com" in (pg.url or ""):
                page = pg
                break
        if page is None:
            page = await ctx.new_page()

        def attach(ws) -> None:
            url = ws.url or ""
            if "ws.fxg.jinritemai.com" not in url:
                return
            print("attached ws listener", url[:100])
            ws.on("framesent", on_frame)

        page.on("websocket", attach)
        for ws in page.context.pages:
            pass  # future connections only via event

        await page.evaluate(_WS_HOOK_INSTALL_JS)

        if do_prepare:
            print("Reloading Feige to restore WS ...")
            if "pc_seller" in (page.url or ""):
                await page.reload(wait_until="domcontentloaded")
            else:
                await page.goto(FEIGE_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(6000)

        from pigeon_protocol.cdp_ws import _WS_STATUS_JS

        st = await page.evaluate(_WS_STATUS_JS)
        print(json.dumps({"ws_status": st, "wait_sec": wait_sec}, ensure_ascii=False))
        print(f"Send 1-2 same-length messages in Feige now ({wait_sec}s) ...")

        t0 = time.time()
        while time.time() - t0 < wait_sec:
            await asyncio.sleep(0.5)

    save_samples(samples)
    print(f"Saved {len(samples)} -> {OUT}")
    _summarize(samples)
    return 0 if samples else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
