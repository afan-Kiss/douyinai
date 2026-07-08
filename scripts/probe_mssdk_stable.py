#!/usr/bin/env python3
"""Probe rc-client-security stable tree for webmssdk / acrawler bundles."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "analysis" / "mssdk_stable_probe.json"

BASE = "https://lf-c-flwb.bytetos.com/obj/rc-client-security/web/stable"
NAMES = [
    "webmssdk.js",
    "mssdk.js",
    "acrawler.js",
    "byted_acrawler.js",
    "frontier.js",
    "webmssdk.umd.js",
]
VERSIONS = [
    "1.0.1.20",
    "1.0.0.65",
    "1.0.0.60",
    "1.0.0.50",
    "2.0.0.1",
    "3.0.0.1",
]


def main() -> int:
    from curl_cffi import requests as curl_requests

    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE, DEFAULT_USER_AGENT

    results = []
    for ver in VERSIONS:
        for name in NAMES:
            url = f"{BASE}/{ver}/{name}"
            try:
                r = curl_requests.head(
                    url,
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                    impersonate=DEFAULT_CURL_IMPERSONATE,
                    timeout=10,
                    allow_redirects=True,
                )
                results.append(
                    {
                        "url": url,
                        "status": r.status_code,
                        "len": r.headers.get("content-length"),
                    }
                )
            except Exception as exc:
                results.append({"url": url, "error": str(exc)[:80]})

    report = {
        "hits": [h for h in results if h.get("status") == 200],
        "probed": len(results),
        "sample": results[:12],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
