#!/usr/bin/env python3
"""CDP harvest — thin CLI wrapper around cdp-onboard (cookies + backstage, optional no-warm)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from pigeon_protocol.cdp_onboard import onboard_async
    from pigeon_protocol.cdp_launch import cdp_port, ensure_cdp_ready
    from pigeon_protocol.runtime_paths import apply_runtime_env

    apply_runtime_env()
    p = argparse.ArgumentParser(description="CDP harvest after im.jinritemai.com login")
    p.add_argument("--wait", type=float, default=300.0)
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--no-launch", action="store_true")
    p.add_argument("--no-warm", action="store_true")
    p.add_argument("--keep-browser", action="store_true")
    args = p.parse_args()

    port = args.port or cdp_port()
    if not ensure_cdp_ready(launch=not args.no_launch, wait_sec=30.0):
        print(json.dumps({"ok": False, "error": f"CDP not ready on {port}"}))
        return 1

    report = asyncio.run(
        onboard_async(
            wait_sec=args.wait,
            launch=False,
            close_browser=not args.keep_browser,
            warm_inners=not args.no_warm,
            export_pack=True,
            port=port,
        )
    )
    out = ROOT / "analysis" / "cdp_login_harvest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
