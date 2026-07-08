#!/usr/bin/env python3
"""Try fxg passport get_qrcode with verifyFp + ttwid bootstrap."""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def main() -> int:
    from curl_cffi import requests as cr

    sess = cr.Session(impersonate="chrome131")
    sess.headers.update({"User-Agent": UA, "Referer": "https://fxg.jinritemai.com/login/common"})

    # Warm cookies from login page
    sess.get("https://fxg.jinritemai.com/login/common", timeout=20)
    sess.get("https://fxg.jinritemai.com/", timeout=20)

    trials = []
    base = "https://fxg.jinritemai.com/passport/web/get_qrcode/"
    configs = [
        {"aid": 4272, "service": "https://fxg.jinritemai.com", "next": "https://fxg.jinritemai.com/"},
        {"aid": 4272, "service": "https://fxg.jinritemai.com/", "next": "https://fxg.jinritemai.com/index.html"},
        {"aid": 2562, "service": "https://im.jinritemai.com", "next": "https://im.jinritemai.com/pc_seller_v2/main/workspace"},
        {"aid": 1383, "service": "https://im.jinritemai.com", "next": "https://im.jinritemai.com/pc_seller_v2/main/workspace"},
        {"aid": 1574, "service": "https://fxg.jinritemai.com", "next": "https://fxg.jinritemai.com/"},
    ]
    for params in configs:
        p = {
            **params,
            "account_sdk_source": "web",
            "sdk_version": "2.2.5",
            "language": "zh",
            "t": int(time.time() * 1000),
        }
        r = sess.get(base, params=p, timeout=15)
        try:
            body = r.json()
        except Exception:
            body = {"raw": (r.text or "")[:300]}
        data = body.get("data") or {}
        trials.append(
            {
                "params": params,
                "error_code": body.get("error_code") or data.get("error_code"),
                "message": body.get("message") or data.get("description"),
                "has_token": bool(data.get("token")),
                "has_qrcode": bool(data.get("qrcode")),
                "cookies": list(sess.cookies.get_dict().keys())[:12],
            }
        )

    print(json.dumps(trials, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
