#!/usr/bin/env python3
"""Pure-protocol foundation audit — algorithm parity checklist."""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ORDER_URL = (
    "https://pigeon.jinritemai.com/backstage/cmpoent/order/query"
    "?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
)
ORDER_BODY = json.dumps(
    {
        "security_user_id": "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk",
        "page_no": 0,
        "page_size": 5,
        "tab_type": 1,
        "biz_type": 2,
        "version": "1.0",
    },
    separators=(",", ":"),
)


def _payload_bytes(s: str) -> int:
    pad = "=" * ((4 - len(s) % 4) % 4)
    t = re.sub(r"[^A-Za-z0-9+/=_-]", "", s).replace("-", "+").replace("_", "/")
    return len(base64.b64decode(t + pad))


def _node_abogus(url: str, body: str = "", method: str = "GET") -> str:
    proc = subprocess.run(
        ["node", str(ROOT / "scripts" / "run_bdms_fetch.mjs"), url, body, method],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if not proc.stdout.strip():
        return ""
    return json.loads(proc.stdout).get("a_bogus") or ""


def audit_abogus() -> dict:
    from pigeon_protocol.foundation.bdms_abogus import FeigeABogus, sign_url_query
    from pigeon_protocol.session import load_session

    q = ORDER_URL.split("?", 1)[1]

    def trial() -> tuple[bool, bool]:
        py_get = FeigeABogus().sign_query(q)
        node_get = _node_abogus(ORDER_URL, "", "GET")
        py_post = FeigeABogus().sign_query(q, ORDER_BODY)
        node_post = _node_abogus(ORDER_URL, ORDER_BODY, "POST")
        g = _payload_bytes(py_get) == _payload_bytes(node_get) if node_get else False
        p = _payload_bytes(py_post) == _payload_bytes(node_post) if node_post else False
        return g, p

    get_ok = post_ok = False
    for _ in range(5):
        g, p = trial()
        get_ok = get_ok or g
        post_ok = post_ok or p
        if get_ok and post_ok:
            break

    signed, _ = sign_url_query(ORDER_URL, session=load_session())
    py_get = FeigeABogus().sign_query(q)
    node_get = _node_abogus(ORDER_URL, "", "GET")
    return {
        "get_payload_bytes_match": get_ok,
        "post_payload_bytes_match": post_ok,
        "browser_fp_len": len(FeigeABogus().browser_fp),
        "python_get_len": len(py_get),
        "node_get_len": len(node_get),
        "python_payload_bytes": _payload_bytes(py_get),
        "node_payload_bytes": _payload_bytes(node_get) if node_get else 0,
        "signed_has_msToken": "msToken=" in signed,
        "signed_has_verifyFp": "verifyFp=" in signed,
        "signed_has_a_bogus": "a_bogus=" in signed,
    }


def audit_jsvmp() -> dict:
    from pigeon_protocol.foundation.bdms_jsvmp import load_program

    prog, meta = load_program()
    total_bc = sum(len(f.bytecode) for f in prog.functions)
    return {
        "string_pool": len(prog.string_pool),
        "functions": len(prog.functions),
        "xor_key": meta.get("xor_key"),
        "total_bytecode_ops": total_bc,
    }


def audit_ws() -> dict:
    from pigeon_protocol.foundation.ws_blob_re import collect_inner_samples
    from pigeon_protocol.foundation.ws_sign_engine import WsSendEngine
    from pigeon_protocol.ws_inner_buckets import classify_inner_bucket
    from pigeon_protocol.ws_sign_bucket import coverage_report

    from pigeon_protocol.ws_template_harvest import text_for_byte_length

    cov = coverage_report()
    samples = collect_inner_samples()
    classified = sum(
        1 for s in samples if classify_inner_bucket(bytes.fromhex(s.inner_hex)) == s.bucket
    )
    canonical = [6, 9, 25, 45, 60, 77, 78]
    engine = WsSendEngine()
    builds = []
    for bl in canonical:
        text = text_for_byte_length(bl) if bl >= 9 else ("好" * 2)[:bl] or "测"
        if len(text.encode("utf-8")) != bl:
            text = "a" * bl
        try:
            payload = engine.build_frame(text)
            builds.append({"textB": bl, "ok": True, "len": len(payload)})
        except Exception as exc:
            builds.append({"textB": bl, "ok": False, "error": str(exc)})
    supported = cov.get("supported_count_1_200", 0)
    return {
        "coverage_1_200": supported,
        "inner_classifier_hits": f"{classified}/{len(samples)}",
        "canonical_builds": builds,
        "strategy": engine.active().name,
        "gap_harvest": cov.get("gap_harvest"),
    }


def audit_session() -> dict:
    from pigeon_protocol.foundation.bdms_tokens import backstage_query_tokens
    from pigeon_protocol.order_relay_headers import load_relay_header_template
    from pigeon_protocol.session import load_session

    session = load_session()
    tokens = backstage_query_tokens(session)
    return {
        "cookies": len(session.cookies),
        "ws_urls": len(session.ws_urls),
        "has_msToken": bool(tokens.get("msToken")),
        "has_verifyFp": bool(tokens.get("verifyFp")),
        "relay_headers": bool(load_relay_header_template()),
    }


def main() -> int:
    report = {
        "abogus": audit_abogus(),
        "jsvmp": audit_jsvmp(),
        "ws": audit_ws(),
        "session": audit_session(),
    }
    ws_cov = report["ws"]["coverage_1_200"]
    checks = [
        report["abogus"]["get_payload_bytes_match"],
        report["abogus"]["post_payload_bytes_match"],
        report["abogus"]["signed_has_msToken"],
        report["jsvmp"]["total_bytecode_ops"] > 0,
        ws_cov >= 55,
        all(x.get("ok") for x in report["ws"]["canonical_builds"]),
    ]
    report["score"] = f"{sum(checks)}/{len(checks)}"
    report["ok"] = all(checks)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
