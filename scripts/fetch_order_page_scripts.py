#!/usr/bin/env python3
"""Fetch backstage order page HTML and list security-related script URLs."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "analysis" / "order_page_scripts.json"

ORDER_URL = (
    "https://fxg.jinritemai.com/ffa/mshop/homepage/index"
    "?source=feige_pc"
)


def main() -> int:
    from curl_cffi import requests as curl_requests

    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE, DEFAULT_USER_AGENT
    from pigeon_protocol.session_store import load_session

    session = load_session()
    cookies = session.cookies if hasattr(session, "cookies") else {}
    if isinstance(cookies, list):
        cookie_hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))
    elif isinstance(cookies, dict):
        cookie_hdr = "; ".join(f"{k}={v}" for k, v in cookies.items())
    else:
        cookie_hdr = ""

    urls_to_try = [
        ORDER_URL,
        "https://im.jinritemai.com/pc_seller_v2/main/workspace",
        "https://pigeon.jinritemai.com/backstage/cmpoent/order/query",
    ]

    report = {"pages": []}
    script_re = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.I)

    for url in urls_to_try:
        try:
            r = curl_requests.get(
                url,
                headers={"User-Agent": DEFAULT_USER_AGENT, "Cookie": cookie_hdr},
                impersonate=DEFAULT_CURL_IMPERSONATE,
                timeout=25,
                allow_redirects=True,
            )
            html = r.text
            scripts = script_re.findall(html)
            sec = [
                s
                for s in scripts
                if any(x in s.lower() for x in ("mssdk", "acrawler", "bdms", "glue", "security", "secsdk"))
            ]
            report["pages"].append(
                {
                    "url": url,
                    "final": str(r.url),
                    "status": r.status_code,
                    "html_len": len(html),
                    "script_count": len(scripts),
                    "security_scripts": sec[:30],
                    "all_scripts_sample": scripts[:15],
                }
            )
        except Exception as exc:
            report["pages"].append({"url": url, "error": str(exc)[:200]})

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
