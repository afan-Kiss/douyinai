#!/usr/bin/env python3
"""Bruteforce FeigeABogus params against live xundan — RE calibration harness."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlunparse, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.conv_list import _unsigned_url, parse_conversation_items
from pigeon_protocol.foundation.bdms_abogus import FeigeABogus
from pigeon_protocol.http_transport import request_json
from pigeon_protocol.order_relay_headers import build_order_relay_headers
from pigeon_protocol.session import load_session


def node_prequery(unsigned: str) -> str:
    proc = subprocess.run(
        ["node", str(ROOT / "scripts" / "run_bdms_fetch.mjs"), unsigned, "", "GET"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    raw = json.loads(proc.stdout)
    pairs = [(k, v) for k, v in parse_qsl(urlparse(raw["capture"]["requestUrl"]).query, keep_blank_values=True) if k != "a_bogus"]
    return urlencode(pairs)


def probe_query(session, unsigned: str, query: str, bogus: str) -> dict:
    hdr = build_order_relay_headers(session, for_method="GET")
    im = session.query_tokens.get("im_pc_version") or ""
    if im:
        hdr["X-IM-PC-Version"] = im
    url = urlunparse(urlparse(unsigned)._replace(query=query + "&a_bogus=" + bogus))
    data = request_json("GET", url, headers=hdr, transport="curl_cffi").get("data") or {}
    return {"code": data.get("code"), "items": len(parse_conversation_items({"data": data}))}


def main() -> int:
    session = load_session()
    unsigned = _unsigned_url(queue_key="no_order", page_size=20, session=session)
    q = node_prequery(unsigned)
    ua = session.user_agent or ""
    hits: list[dict] = []

    for o0 in (0, 1, 2, 3):
        for o1 in (0, 1):
            for o2 in (0, 8, 14, 16):
                for ua_key in (bytes([0, 1, 8]), bytes([0, 1, 0]), bytes([0, 1, 14])):
                    bogus = FeigeABogus(user_agent=ua, options=(o0, o1, o2), ua_key=ua_key).sign_query(q)
                    out = probe_query(session, unsigned, q, bogus)
                    if out.get("items") or str(out.get("code")) in ("0", "0.0"):
                        hits.append({"options": (o0, o1, o2), "ua_key": list(ua_key), **out})

    report = {"query_len": len(q), "hits": hits, "tested": 4 * 2 * 4 * 3}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
