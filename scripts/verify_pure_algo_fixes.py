#!/usr/bin/env python3
"""Verify pure-algorithm fixes: pigeon_sign, inner seed, frontier bypass."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from pigeon_protocol.session import load_session
    from pigeon_protocol.foundation.pigeon_sign_service import ensure_pigeon_sign, bootstrap_sign_from_templates
    from pigeon_protocol.foundation.pigeon_sdk_delegate import ensure_send_inner
    from pigeon_protocol.foundation.ws_frontier_sign import frontier_sign_status, template_send_bypass

    session = load_session()
    report = {
        "pigeon_sign_template": bootstrap_sign_from_templates(session),
        "pigeon_sign_ensure": ensure_pigeon_sign(session),
        "inner_seed": ensure_send_inner(session, cdp_if_available=False),
        "frontier": frontier_sign_status(session),
        "template_bypass": template_send_bypass(),
    }
    report["ok"] = (
        report["pigeon_sign_ensure"].get("ok")
        and report["inner_seed"].get("ok")
        and report["template_bypass"]
    )
    out = ROOT / "analysis" / "pure_algo_fix_verify.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
