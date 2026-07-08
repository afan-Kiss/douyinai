#!/usr/bin/env python3
"""Download Feige IM SDK webpack chunks and rank sign-related modules."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "analysis" / "im_sdk"
MANIFEST = ROOT / "analysis" / "im_sdk_manifest.json"

KEYWORDS: list[tuple[str, int]] = [
    ("s:client_message_id", 50),
    ("client_message_id", 2),
    ("WebSocket", 3),
    ("pigeon_sign", 10),
    ("wasm", 20),
    ("instantiate", 5),
    ("get_message_by_init", 20),
    ("IMCloudSend", 8),
    ("sign", 1),
    ("base64", 2),
    ("169", 1),
    ("226", 1),
]


def main() -> int:
    from curl_cffi import requests as curl_requests

    from pigeon_protocol.feige_init import _fetch_workspace_html
    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE, DEFAULT_USER_AGENT
    from pigeon_protocol.session import load_session

    session = load_session()
    html = _fetch_workspace_html(session)
    urls = re.findall(
        r"https://lf3-fe\.ecombdstatic\.com/obj/ecom-cdn-default/ecom-diansahng-im/ecom_im_pc/static/js/[^\"']+\.js",
        html,
    )
    OUT.mkdir(parents=True, exist_ok=True)
    ranked: list[dict] = []

    for url in sorted(set(urls)):
        name = url.rsplit("/", 1)[-1]
        path = OUT / name
        if not path.is_file():
            resp = curl_requests.get(
                url,
                headers={
                    "User-Agent": session.user_agent or DEFAULT_USER_AGENT,
                    "Referer": "https://im.jinritemai.com/",
                },
                impersonate=DEFAULT_CURL_IMPERSONATE,
                timeout=60,
            )
            if resp.status_code != 200:
                continue
            path.write_bytes(resp.content)

        text = path.read_text(encoding="utf-8", errors="ignore")
        hits: dict[str, int] = {}
        score = 0
        for key, weight in KEYWORDS:
            count = len(re.findall(re.escape(key), text, re.I))
            if count:
                hits[key] = count
                score += count * weight
        if score:
            ranked.append(
                {
                    "file": name,
                    "bytes": path.stat().st_size,
                    "score": score,
                    "hits": hits,
                }
            )

    ranked.sort(key=lambda x: x["score"], reverse=True)
    manifest = {"chunk_count": len(ranked), "chunks": ranked}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
