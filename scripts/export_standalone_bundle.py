#!/usr/bin/env python3
"""Export order cache + standalone bundle via CDP (bootstrap helper)."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

USER_ID = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"
BUNDLE = ROOT / "standalone_bundle"


async def main() -> int:
    from pigeon_protocol.cdp_bridge import CdpBridge, cdp_ready
    from pigeon_protocol.config import SESSION_FILE
    from pigeon_protocol.offline_order_cache import save_order_cache
    from pigeon_protocol.pure_runtime import _order_body, _order_unsigned_url
    from pigeon_protocol.session import load_session

    if not cdp_ready():
        print("CDP not ready", file=sys.stderr)
        return 1

    session = load_session()
    unsigned = _order_unsigned_url()
    body = _order_body(USER_ID)
    bridge = CdpBridge(session)

    from pigeon_protocol.http_transport import order_api_ok
    from pigeon_protocol.order_curl_relay import query_orders_via_curl_relay
    from pigeon_protocol.order_sign_snapshot import save_sign_snapshot

    raw = query_orders_via_curl_relay(session, USER_ID)
    source = "curl_relay/export"
    if not order_api_ok(raw):
        raw = await bridge._page_fetch(url=unsigned, method="POST", body=body)
        source = "cdp/export"
    else:
        cap = raw.get("_capture") if isinstance(raw.get("_capture"), dict) else None
        if cap:
            save_sign_snapshot(url=cap["url"], headers=cap["headers"], sample_body=body, source=source)
    path = save_order_cache(USER_ID, raw, source=source)
    print(json.dumps({"cache": str(path), "ok": raw.get("ok"), "preview": str(raw.get("text") or "")[:200]}, ensure_ascii=False, indent=2))

    BUNDLE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SESSION_FILE, BUNDLE / "session.json")
    tpl_dir = BUNDLE / "ws_sign"
    tpl_dir.mkdir(exist_ok=True)
    for p in (ROOT / "captures" / "live" / "ws_sign").glob("live_ws_frame_sent_b*.json"):
        shutil.copy2(p, tpl_dir / p.name)
    orders_dir = BUNDLE / "orders"
    orders_dir.mkdir(exist_ok=True)
    dest = orders_dir / path.name
    if dest.resolve() != path.resolve():
        shutil.copy2(path, dest)

    from pigeon_protocol.order_sign_snapshot import SNAPSHOT_FILE

    if SNAPSHOT_FILE.exists():
        shutil.copy2(SNAPSHOT_FILE, BUNDLE / "order_sign_snapshot.json")

    env_src = ROOT / "analysis" / "bdms_browser_env.json"
    fp_src = ROOT / "analysis" / "browser_fingerprint.json"
    if env_src.exists():
        shutil.copy2(env_src, BUNDLE / "bdms_browser_env.json")
    if fp_src.exists():
        shutil.copy2(fp_src, BUNDLE / "browser_fingerprint.json")

    har_ctx = ROOT / "captures" / "live" / "from_har" / "har_00327_http_body.json"
    if har_ctx.exists():
        import json as _json

        event = _json.loads(har_ctx.read_text(encoding="utf-8"))
        post = str(event.get("post_data") or "")
        if post:
            (BUNDLE / "get_by_conversation_body.bin").write_bytes(post.encode("latin-1"))
            print("exported get_by_conversation_body.bin")

    print(f"bundle -> {BUNDLE}")
    return 0 if raw.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
