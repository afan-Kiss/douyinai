#!/usr/bin/env python3
"""Download sdk-glue.js and scan for acrawler / frontierSign loaders."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "analysis" / "sdk_glue_scan.json"
GLUE_URL = "https://lf-c-flwb.bytetos.com/obj/rc-client-security/web/glue/1.0.0.65/sdk-glue.js"


def main() -> int:
    from curl_cffi import requests as curl_requests

    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE, DEFAULT_USER_AGENT

    resp = curl_requests.get(
        GLUE_URL,
        headers={"User-Agent": DEFAULT_USER_AGENT, "Referer": "https://im.jinritemai.com/"},
        impersonate=DEFAULT_CURL_IMPERSONATE,
        timeout=30,
    )
    text = resp.text
    glue_path = ROOT / "analysis" / "sdk-glue.js"
    glue_path.write_text(text, encoding="utf-8")

    keywords = ["acrawler", "frontierSign", "mssdk", "byted_acrawler", "webmssdk", "bdms", "aid", "1383", "30026"]
    hits = {k: len(re.findall(re.escape(k), text, re.I)) for k in keywords}
    urls = re.findall(r"https?://[a-zA-Z0-9_./?=&%-]+", text)
    security_urls = [u for u in urls if any(x in u for x in ("security", "mssdk", "acrawler", "bdms", "bytegoofy"))]

    report = {
        "url": GLUE_URL,
        "bytes": len(text.encode("utf-8")),
        "hits": hits,
        "security_urls": sorted(set(security_urls))[:30],
        "saved": str(glue_path),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
