#!/usr/bin/env python3
"""Scan bdms.js / sdk-glue.js for mssdk / acrawler CDN URLs."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis" / "mssdk_url_probe.json"


def extract_urls(text: str) -> list[str]:
    return sorted(
        set(
            re.findall(
                r"https?://[a-zA-Z0-9_./?=&%-]+(?:mssdk|acrawler|frontier|webmssdk|security|bdms)[a-zA-Z0-9_./?=&%-]*",
                text,
                re.I,
            )
        )
    )


def main() -> int:
    files = [
        ROOT / "analysis" / "bdms.js",
        ROOT / "analysis" / "sdk-glue.js",
    ]
    report: dict = {"files": {}}
    for fp in files:
        if not fp.is_file():
            continue
        text = fp.read_text(encoding="utf-8", errors="ignore")
        report["files"][fp.name] = {
            "bytes": len(text.encode("utf-8")),
            "urls": extract_urls(text)[:40],
            "mssdk_host_hits": text.lower().count("mssdk.bytedance"),
            "frontier_hits": len(re.findall("frontier", text, re.I)),
            "acrawler_hits": len(re.findall("acrawler", text, re.I)),
        }

    # probe known stable version dirs
    candidates = [
        "https://lf-c-flwb.bytetos.com/obj/rc-client-security/web/stable/1.0.1.20/bdms.js",
        "https://lf-c-flwb.bytetos.com/obj/rc-client-security/web/glue/1.0.0.65/sdk-glue.js",
        "https://lf3-cdn-tos.bytegoofy.com/obj/goofy/secsdk/secsdk-lastest.umd.js",
    ]
    try:
        from curl_cffi import requests as curl_requests

        from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE, DEFAULT_USER_AGENT

        probes = []
        for url in candidates:
            try:
                r = curl_requests.head(
                    url,
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                    impersonate=DEFAULT_CURL_IMPERSONATE,
                    timeout=12,
                    allow_redirects=True,
                )
                probes.append({"url": url, "status": r.status_code, "len": r.headers.get("content-length")})
            except Exception as exc:
                probes.append({"url": url, "error": str(exc)[:120]})
        report["head_probes"] = probes
    except Exception as exc:
        report["head_probes_error"] = str(exc)

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    raise SystemExit(main())
