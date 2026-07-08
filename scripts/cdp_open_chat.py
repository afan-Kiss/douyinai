#!/usr/bin/env python3
"""Open buyer chat in Feige via CDP (for template harvest / WS sign RE)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


async def main(port: int = 9222, *, uid: str | None = None) -> dict:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready
    from pigeon_protocol.ws_template_harvest import DEFAULT_HARVEST_UID, _ensure_chat_open

    if not cdp_ready(port):
        return {"ok": False, "error": "cdp_not_ready"}

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        page = next(
            (pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")),
            None,
        )
        if not page:
            return {"ok": False, "error": "no_feige_page"}
        result = await _ensure_chat_open(page, uid=uid or DEFAULT_HARVEST_UID)
        result["page"] = page.url
        return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--uid", default="", help="buyer UID for class-based row match")
    args = ap.parse_args()
    r = asyncio.run(main(args.port, uid=args.uid or None))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    raise SystemExit(0 if r.get("ok") else 1)
