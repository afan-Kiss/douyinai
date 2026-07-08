#!/usr/bin/env python3
"""One-shot CDP session sync → session/session.json"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pigeon_protocol.protocol_client import ProtocolClient  # noqa: E402


def main() -> int:
    client = ProtocolClient()
    report = client.prepare(force_cdp=True)
    print(json.dumps({**report, "health": client.health()}, ensure_ascii=False, indent=2))
    return 0 if report.get("cookies") else 1


if __name__ == "__main__":
    raise SystemExit(main())
