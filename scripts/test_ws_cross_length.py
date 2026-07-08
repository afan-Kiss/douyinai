#!/usr/bin/env python3
"""Test whether 9-60B templates share inner blob — cross-length send."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

USER_ID = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"


def main() -> int:
    from pigeon_protocol.capture_loader import load_send_template
    from pigeon_protocol.ws_sign_decode import decode_blob, compare_inners
    from pigeon_protocol.pure_runtime import PureProtocolRuntime

    t9 = load_send_template(9)
    t18 = load_send_template(18)
    if not t9 or not t18:
        print("missing templates")
        return 1

    b9 = decode_blob(t9["frame_hex"])
    b18 = decode_blob(t18["frame_hex"])
    cmp = compare_inners(b9, b18)
    print("inner 9 vs 18:", json.dumps(cmp, indent=2))

    rt = PureProtocolRuntime()
    text18 = "您好，已收到您的消息"  # 18 bytes UTF-8
    assert len(text18.encode("utf-8")) == 18

    # Force use b009 template for 18B text (normally would pick b018)
    from pigeon_protocol.send import SendService

    svc = SendService(rt.session)
    result = svc.send_text(
        text18,
        security_user_id=USER_ID,
        template_byte_len=9,  # cross-length experiment
        auto_harvest=False,
    )
    print("cross-length send (9B template, 18B text):", json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
