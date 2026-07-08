#!/usr/bin/env python3
"""Ingest analysis/pigeon_rust_hook.json → session inner cache."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from pigeon_protocol.foundation.ws_cdp_inner_ingest import ingest_hook_file
    from pigeon_protocol.session import load_session

    session = load_session()
    report = ingest_hook_file(session, ROOT / "analysis" / "pigeon_rust_hook.json")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
