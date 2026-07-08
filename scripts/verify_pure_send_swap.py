#!/usr/bin/env python3
"""Verify pure send: swap real text within same byte-length bucket."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

UID = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"


def send(text: str) -> dict:
    from pigeon_protocol.config import AppConfig
    from pigeon_protocol.standalone import StandaloneRuntime

    r = StandaloneRuntime(config=AppConfig(dry_run=False)).send_text(text, security_user_id=UID)
    return {"text": text, "byte_len": len(text.encode()), "ok": r.ok, "payload_length": r.payload_length, "ack": r.raw}


def main() -> None:
    tests = [
        ("您好，已收到", 18),  # 3+3+3+3+3+3 = 18
        ("收到，马上处理", 21),  # need exact 21 bytes
    ]
    # fix 21B: 收到(6)+，(3)+马上(6)+处理(6) = 21
    tests[1] = ("收到，马上处理", len("收到，马上处理".encode()))

    import json

    print(json.dumps([send(t[0]) for t in tests], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
