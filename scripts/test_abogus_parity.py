#!/usr/bin/env python3
"""Compare pure-Python a_bogus vs Node bdms + live order relay."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TEST_URL = (
    "https://pigeon.jinritemai.com/backstage/cmpoent/order/query"
    "?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
)


def node_sign(url: str) -> dict:
    from pigeon_protocol.foundation.bdms_sign import sign_backstage_url

    r = sign_backstage_url(url, method="GET")
    return {"ok": r.ok, "signed_url": r.signed_url, "tokens": r.tokens, "via": r.via, "error": r.error}


def python_sign(url: str) -> dict:
    from pigeon_protocol.foundation.bdms_abogus import sign_url_query

    signed, bogus = sign_url_query(url, method="GET")
    return {"ok": bool(bogus), "signed_url": signed, "a_bogus": bogus, "via": "python_abogus"}


def order_probe(signed_url: str) -> dict:
    from pigeon_protocol.foundation.relay_client import BackstageRelayClient
    from pigeon_protocol.session import load_session

    client = BackstageRelayClient(load_session())
    resp = client.get(signed_url)
    return {
        "ok": resp.api_ok(),
        "status": resp.status,
        "code": resp.api_code(),
        "via": resp.via,
        "error": resp.error,
    }


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else TEST_URL
    report: dict = {"url": url[:120]}

    py = python_sign(url)
    report["python"] = py
    node = node_sign(url)
    report["node"] = {k: node[k] for k in ("ok", "via", "error")}
    if node.get("tokens"):
        report["node"]["a_bogus_len"] = len(node["tokens"].get("a_bogus", ""))
    report["python"]["a_bogus_len"] = len(py.get("a_bogus", ""))

    # Node baseline via direct subprocess (sign_backstage_url prefers Python)
    import subprocess
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        ["node", str(root / "scripts" / "run_bdms_fetch.mjs"), url, "", "GET"],
        capture_output=True,
        text=True,
        cwd=str(root),
    )
    if proc.stdout.strip():
        node_direct = json.loads(proc.stdout)
        report["node_direct"] = {
            "a_bogus_len": len(node_direct.get("a_bogus", "")),
            "partial": node_direct.get("partial"),
        }

    if py.get("signed_url"):
        report["python_order"] = order_probe(py["signed_url"])
    if node.get("signed_url"):
        report["node_order"] = order_probe(node["signed_url"])

    print(json.dumps(report, ensure_ascii=False, indent=2))
    py_ok = report.get("python_order", {}).get("ok")
    node_ok = report.get("node_order", {}).get("ok")
    return 0 if py_ok or node_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
