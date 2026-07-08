"""Foundation layer health — architecture / RE readiness matrix."""
from __future__ import annotations

from typing import Any

from pigeon_protocol.foundation.bdms_sign import node_available
from pigeon_protocol.foundation.types import FoundationReport
from pigeon_protocol.foundation.ws_sign_engine import WsSendEngine


def foundation_report(session) -> FoundationReport:
    from pigeon_protocol.http_transport import curl_cffi_available
    from pigeon_protocol.order_relay_headers import load_relay_header_template
    from pigeon_protocol.pure_config import node_sign_allowed, pure_only_mode, relay_headers_from_hints

    ws_cap = WsSendEngine().capability()
    curl_ok = curl_cffi_available()
    relay = bool(load_relay_header_template()) or (relay_headers_from_hints() and curl_ok)
    node = node_available() and node_sign_allowed()

    from pigeon_protocol.foundation.ws_blob_re import re_status
    from pigeon_protocol.ws_sign_bucket import coverage_report

    cov = coverage_report()
    re_info = re_status()

    jsvmp_info: dict[str, Any] = {"parsed": False}
    python_abogus = False
    try:
        from pigeon_protocol.foundation.bdms_abogus import FeigeABogus
        from pigeon_protocol.foundation.bdms_jsvmp import deep_report, load_program

        prog, jmeta = load_program()
        dr = deep_report(prog)
        probe = FeigeABogus().sign_query("device_platform=web&aid=1383")
        python_abogus = len(probe) >= 100 and len(FeigeABogus().browser_fp) == 85
        jsvmp_info = {
            "parsed": True,
            "string_pool": len(prog.string_pool),
            "functions": len(prog.functions),
            "xor_key": jmeta.get("xor_key"),
            "sign_fn_candidates": [x["fn"] for x in dr.get("sign_xrefs", [])[:8]],
            "sign_pipeline": {
                "create_fn": 103,
                "sign_core_fn": 150,
                "xhr_send_fn": 107,
                "handleUrl_fn": 115,
                "salt": "dhzx",
                "aid": 1383,
                "pageId": 30026,
                "browser_fp_len": len(FeigeABogus().browser_fp),
            },
            "keywords_hit": sorted(dr.get("keywords", {}).keys())[:12],
        }
    except Exception as exc:
        jsvmp_info = {"parsed": False, "error": str(exc)}

    blockers: list[str] = []
    re_targets = [
        "Pigeon Rust packedMessage offline crypto (Feige Electron binary RE)",
    ]
    if not python_abogus:
        re_targets.insert(0, "bdms scope.create → Python a_bogus (fn#103/#150)")
    else:
        re_targets.insert(0, "order live API: 10001010A = session/IP risk (sign algo parity OK)")

    if not node and not python_abogus:
        blockers.append("bdms: no sign backend (enable Python a_bogus or install Node bdms)")
    if not curl_ok:
        blockers.append("transport: curl_cffi not installed")
    if not relay and not relay_headers_from_hints():
        blockers.append("relay: import HAR/bootstrap for chrome hints")
    if not session.cookies:
        blockers.append("session: no cookies")
    if not session.ws_urls:
        blockers.append("session: no ws_url — run session-doctor or qr-login")
    if not ws_cap.ready:
        blockers.append("ws send: missing canonical bucket templates (b006/b009/b077/b078)")
    gap_sample = (cov.get("gaps_1_200") or [])[:8]
    re_gaps: list[str] = []
    if gap_sample:
        re_gaps.append(f"ws textB gaps (sample): {gap_sample}… — CDP harvest or 226B RE")

    http_sign = {
        "node_bdms": node,
        "python_abogus": python_abogus,
        "sign_available": node or python_abogus,
        "offline_sign": "node_jsdom" if node else ("python_abogus" if python_abogus else "none"),
        "pure_only": pure_only_mode(),
        "csrf_auto_head": curl_ok,
        "relay_template": relay,
        "sign_entry": "foundation.bdms_sign.sign_backstage_url",
        "transport_entry": "foundation.relay_client.BackstageRelayClient",
    }

    ok = (
        curl_ok
        and relay
        and bool(session.cookies)
        and bool(session.ws_urls)
        and ws_cap.ready
        and python_abogus
    )

    return FoundationReport(
        ok=ok,
        bdms_node=node,
        curl_cffi=curl_ok,
        relay_headers=relay,
        session_cookies=len(session.cookies),
        ws_urls=len(session.ws_urls),
        ws_send=ws_cap,
        http_sign={
            **http_sign,
            "ws_coverage_1_200": cov.get("supported_count_1_200"),
            "ws_re": re_info,
            "ws_gap_harvest": cov.get("gap_harvest"),
            "bdms_jsvmp": jsvmp_info,
            "python_abogus": python_abogus,
        },
        blockers=blockers,
        re_targets=re_targets + re_gaps,
    )
