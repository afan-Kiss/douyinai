#!/usr/bin/env python3
"""Pure-protocol Rust SDK invoke probe (HTTP session only, no browser/client)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis" / "feige_rust_invoke_report.json"


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from pigeon_protocol.foundation.rust_sdk_inner import invoke_create_message
    from pigeon_protocol.session import load_session

    session = load_session()
    report = invoke_create_message(session)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
