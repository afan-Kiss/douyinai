#!/usr/bin/env python3
"""Test CdpSigner and compare httpx vs page fetch."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from pigeon_protocol.sign import CdpSigner, apply_sign_tokens, parse_sign_tokens

URL = (
    "https://pigeon.jinritemai.com/backstage/cmpoent/order/query"
    "?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
)


def main() -> int:
    signer = CdpSigner()
    if not signer.available():
        print("CDP not ready", file=sys.stderr)
        return 1
    tokens = signer.sign_tokens(URL, method="POST", body="{}")
    signed = apply_sign_tokens(URL, tokens)
    print(json.dumps({"tokens": {k: v[:60] for k, v in tokens.items()}, "signed_head": signed[:200]}, ensure_ascii=False, indent=2))

    import httpx
    from pigeon_protocol.session import load_session

    s = load_session()
    headers = {
        "User-Agent": s.user_agent,
        "Content-Type": "application/json;charset=UTF-8",
        "Referer": s.headers.get("Referer", "https://im.jinritemai.com/"),
        "Origin": "https://im.jinritemai.com",
        "Cookie": s.cookie_header(),
    }
    headers.update(s.headers)
    r = httpx.post(signed, headers=headers, json={}, timeout=15)
    data = r.json()
    print("httpx code:", data.get("code"), "msg:", data.get("msg"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
