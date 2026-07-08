#!/usr/bin/env python3
"""Run all gap workstreams — baseline + probes for remaining algorithms."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

KNOWN_BUYER = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"


def _run_node(script: str, *, env: dict | None = None, timeout: int = 90) -> dict:
    path = ROOT / "scripts" / script
    if not path.is_file():
        return {"ok": False, "error": f"missing {script}"}
    proc = subprocess.run(
        ["node", str(path)],
        cwd=str(ROOT),
        env=env or os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    out = (proc.stdout or "").strip()
    try:
        data = json.loads(out) if out else {}
    except json.JSONDecodeError:
        data = {"raw": out[:2000], "stderr": (proc.stderr or "")[:800]}
    data["exit"] = proc.returncode
    return data


def main() -> int:
    os.environ.setdefault("PIGEON_STANDALONE", "1")
    report: dict = {"workstreams": {}}

    from pigeon_protocol.foundation.status import foundation_report
    from pigeon_protocol.session import load_session

    session = load_session()
    report["foundation"] = foundation_report(session).to_dict()

    # P0 Rust SDK
    try:
        from pigeon_protocol.foundation.rust_sdk_inner import invoke_create_message

        report["workstreams"]["rust_sdk"] = invoke_create_message(
            session,
            conversation_id="",
            text="好",
            timeout_sec=45,
        )
    except Exception as exc:
        report["workstreams"]["rust_sdk"] = {"ok": False, "error": str(exc)}

    # P1 pure relay xundan (no CDP)
    os.environ["PIGEON_NO_CDP"] = "1"
    try:
        from pigeon_protocol.conv_list import list_conversations_relay

        relay = list_conversations_relay(session, size=20, queue_keys=("no_order", "no_pay"))
        report["workstreams"]["xundan_pure_relay"] = {
            "ok": relay.get("ok"),
            "via": relay.get("via"),
            "items": len(relay.get("items") or []),
            "api_code": relay.get("api_code"),
            "error": relay.get("error"),
        }
    except Exception as exc:
        report["workstreams"]["xundan_pure_relay"] = {"ok": False, "error": str(exc)}
    finally:
        os.environ.pop("PIGEON_NO_CDP", None)

    # P1 xundan curl relay (CDP sign only)
    try:
        from pigeon_protocol.conv_xundan_curl_relay import fetch_xundan_via_curl_relay

        curl = fetch_xundan_via_curl_relay(session, queue_key="no_order", page_size=20)
        report["workstreams"]["xundan_curl_relay"] = {
            "ok": curl.get("ok"),
            "code": curl.get("api_code"),
            "items": len(curl.get("items") or []),
        }
    except Exception as exc:
        report["workstreams"]["xundan_curl_relay"] = {"ok": False, "error": str(exc)}

    # P1 WS gap probe
    from pigeon_protocol.ws_sign_bucket import gap_harvest_plan, coverage_report

    report["workstreams"]["ws_coverage"] = coverage_report()
    report["workstreams"]["ws_gaps"] = gap_harvest_plan(probe_build=True)

    # P1 frontierSign
    stub = json.dumps({"X-MS-STUB": "d41d8cd98f00b204e9800998ecf8427e"})
    for script in ("run_frontier_glue.mjs", "run_frontier_sign.mjs"):
        try:
            proc = subprocess.run(
                ["node", str(ROOT / "scripts" / script)],
                input=stub,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=45,
            )
            data = json.loads(proc.stdout or "{}")
            report["workstreams"][f"frontier_{script}"] = {
                "ok": bool(data.get("ok") or data.get("headers")),
                "headers": list((data.get("headers") or {}).keys())[:6],
            }
        except Exception as exc:
            report["workstreams"][f"frontier_{script}"] = {"ok": False, "error": str(exc)[:120]}

    # P2 msToken / pigeon_sign
    from pigeon_protocol.foundation.bdms_tokens import backstage_query_tokens
    from pigeon_protocol.foundation.pigeon_sign_service import bootstrap_sign_from_templates

    tokens = backstage_query_tokens(session)
    report["workstreams"]["tokens"] = {
        "has_msToken": bool(tokens.get("msToken")),
        "has_verifyFp": bool(tokens.get("verifyFp")),
        "has_pigeon_sign": bool(session.query_tokens.get("pigeon_sign")),
        "pigeon_sign_bootstrap": bootstrap_sign_from_templates(session),
    }

    # Orders probe
    try:
        from pigeon_protocol.foundation.relay_client import BackstageRelayClient
        from pigeon_protocol.config import ORDER_QUERY_PATH, PIGEON_HOST

        unsigned = (
            f"{PIGEON_HOST}{ORDER_QUERY_PATH}"
            "?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
        )
        body = {
            "security_user_id": KNOWN_BUYER,
            "page_no": 0,
            "page_size": 3,
            "tab_type": 1,
            "biz_type": 2,
            "version": "1.0",
        }
        relay = BackstageRelayClient(session).post(unsigned, body, via="gap_probe/orders")
        report["workstreams"]["orders_relay"] = {
            "ok": relay.ok,
            "code": relay.api_code(),
            "sign_via": relay.sign.via if relay.sign else None,
        }
    except Exception as exc:
        report["workstreams"]["orders_relay"] = {"ok": False, "error": str(exc)}

    pending = []
    if not report["workstreams"].get("rust_sdk", {}).get("ok"):
        pending.append("P0: Rust packedMessage / 169B inner offline")
    if not report["workstreams"].get("xundan_pure_relay", {}).get("ok"):
        pending.append("P1: xundan pure relay whale 11001")
    ws_cov = report["workstreams"].get("ws_coverage", {}).get("supported_count_1_200", 0)
    if ws_cov < 200:
        pending.append(f"P1: WS textB coverage {ws_cov}/200")
    if not report["workstreams"].get("frontier_run_frontier_glue.mjs", {}).get("ok"):
        pending.append("P1: frontierSign pure compute")
    if not tokens.get("msToken"):
        pending.append("P2: msToken cold-start generation")
    report["pending"] = pending
    report["ok"] = len(pending) == 0

    out_path = ROOT / "analysis" / "gap_workstreams_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "pending": pending, "report": str(out_path)}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
