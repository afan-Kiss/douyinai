#!/usr/bin/env python3
"""Probe Feige/Doudian login SSO endpoints."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def main() -> int:
    from curl_cffi import requests as cr

    seeds = [
        "https://im.jinritemai.com/pc_seller_v2/main/workspace",
        "https://fxg.jinritemai.com/login/common",
        "https://fxg.jinritemai.com/",
        "https://im.jinritemai.com/",
    ]
    report: dict = {"redirects": [], "sso_hits": []}
    for u in seeds:
        try:
            r = cr.get(
                u,
                impersonate="chrome131",
                allow_redirects=True,
                timeout=20,
                headers={"User-Agent": UA},
            )
            chain = [u, str(r.url)]
            report["redirects"].append({"start": u, "final": str(r.url), "status": r.status_code})
            text = r.text or ""
            for pat in (
                r"https://sso\.[a-z0-9.-]+/[^\"'\s<>]+",
                r"https://[^\"'\s]*passport[^\"'\s]*",
                r"get_qrcode[^\"'\s]*",
                r"check_qrconnect[^\"'\s]*",
                r'"aid"\s*:\s*\d+',
                r"aid=\d+",
            ):
                for m in re.findall(pat, text[:200000], flags=re.I):
                    if m not in report["sso_hits"]:
                        report["sso_hits"].append(m[:300])
        except Exception as exc:
            report["redirects"].append({"start": u, "error": str(exc)})

    # Try known doudian SSO hosts
    candidates = [
        "https://fxg.jinritemai.com/passport/web/get_qrcode/",
        "https://sso.oceanengine.com/get_qrcode/",
        "https://sso.bytedance.com/get_qrcode/",
    ]
    report["probe_get_qrcode"] = []
    for url in candidates:
        try:
            r = cr.get(url, impersonate="chrome131", timeout=15, headers={"User-Agent": UA})
            report["probe_get_qrcode"].append(
                {"url": url, "status": r.status_code, "body": (r.text or "")[:400]}
            )
        except Exception as exc:
            report["probe_get_qrcode"].append({"url": url, "error": str(exc)})

    # Fetch login page script bundles for passport URLs
    try:
        r = cr.get(
            "https://fxg.jinritemai.com/login/common",
            impersonate="chrome131",
            timeout=20,
            headers={"User-Agent": UA},
        )
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', r.text or "")
        report["login_scripts"] = scripts[:20]
        for src in scripts[:8]:
            if not src.startswith("http"):
                src = "https://fxg.jinritemai.com" + src
            try:
                js = cr.get(src, impersonate="chrome131", timeout=20, headers={"User-Agent": UA}).text or ""
                for m in re.findall(r"passport/web/[a-z_]+", js):
                    if m not in report["sso_hits"]:
                        report["sso_hits"].append(m)
                for m in re.findall(r"aid[\"']?\s*[:=]\s*[\"']?(\d+)", js):
                    report.setdefault("aids", []).append(m)
            except Exception:
                continue
    except Exception as exc:
        report["login_page_error"] = str(exc)

    # Try fxg passport get_qrcode with common aids
    aids = sorted(set(report.get("aids") or []) | {"1383", "4272", "3386", "2562", "1574", "1215"})
    report["get_qrcode_trials"] = []
    service = "https://fxg.jinritemai.com"
    next_url = "https://fxg.jinritemai.com/"
    for aid in aids:
        params = {
            "aid": aid,
            "service": service,
            "next": next_url,
            "account_sdk_source": "web",
            "sdk_version": "2.2.5",
        }
        try:
            r = cr.get(
                "https://fxg.jinritemai.com/passport/web/get_qrcode/",
                params=params,
                impersonate="chrome131",
                timeout=15,
                headers={"User-Agent": UA, "Referer": "https://fxg.jinritemai.com/login/common"},
            )
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            report["get_qrcode_trials"].append(
                {
                    "aid": aid,
                    "status": r.status_code,
                    "error_code": body.get("error_code") or (body.get("data") or {}).get("error_code"),
                    "message": body.get("message") or (body.get("data") or {}).get("description"),
                    "has_token": bool((body.get("data") or {}).get("token")),
                }
            )
        except Exception as exc:
            report["get_qrcode_trials"].append({"aid": aid, "error": str(exc)})

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
