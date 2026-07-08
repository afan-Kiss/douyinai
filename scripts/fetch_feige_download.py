#!/usr/bin/env python3
"""Probe official pages/APIs for Feige / 抖店 desktop installer URLs."""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis" / "feige_download_probe.json"

PAGES = [
    "https://im.jinritemai.com/download",
    "https://im.jinritemai.com/pc_app_download",
    "https://darenim.jinritemai.com/v2/daren_download",
    "https://fxg.jinritemai.com/ffa/mshop/homepage/index",
]

APIS = [
    "https://fxg.jinritemai.com/api/app/client/download?platform=windows",
    "https://im.jinritemai.com/api/pc/download?platform=windows",
    "https://im.jinritemai.com/pigeon_im/v1/client/download?platform=windows",
]


def fetch(url: str) -> tuple[int, str, dict]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
            ctype = r.headers.get("Content-Type", "")
            if "json" in ctype or body[:1] in (b"{", b"["):
                return r.status, body.decode("utf-8", "replace"), dict(r.headers)
            return r.status, body.decode("utf-8", "replace"), dict(r.headers)
    except Exception as exc:
        return 0, str(exc), {}


def extract_urls(text: str) -> list[str]:
    pats = [
        r"https?://[^\s\"'<>]+\.(?:exe|dmg|zip|msi)",
        r"https?://[^\s\"'<>]*(?:download|setup|installer|package|tos)[^\s\"'<>]*",
        r"wss?://[^\s\"'<>]+",
    ]
    found: list[str] = []
    for pat in pats:
        for m in re.findall(pat, text, re.I):
            m = m.rstrip("\\),.;'\"")
            if m not in found and len(m) < 500:
                found.append(m)
    return found


def main() -> int:
    report: dict = {"pages": [], "apis": [], "exe_candidates": []}
    for url in PAGES:
        status, body, headers = fetch(url)
        urls = extract_urls(body)
        m = re.search(r"<title>([^<]+)", body, re.I)
        report["pages"].append(
            {
                "url": url,
                "status": status,
                "len": len(body),
                "title": m.group(1)[:80] if m else "",
                "urls": urls[:30],
                "snippet": body[:500] if status and len(body) < 2000 else "",
            }
        )
        report["exe_candidates"].extend(u for u in urls if re.search(r"\.exe|setup|installer", u, re.I))

    for url in APIS:
        status, body, _ = fetch(url)
        entry = {"url": url, "status": status, "body": body[:2000]}
        try:
            entry["json"] = json.loads(body)
        except Exception:
            pass
        report["apis"].append(entry)
        report["exe_candidates"].extend(extract_urls(body))

    report["exe_candidates"] = sorted(set(report["exe_candidates"]))[:50]
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
