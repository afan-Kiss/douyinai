#!/usr/bin/env python3
"""P1: Map init response 169B inners → A–G send equivalence classes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "analysis" / "init_inner_mapping.json"


def main() -> int:
    from pigeon_protocol.foundation.init_inner_mapper import (
        build_session_init_mapping,
        export_init_mapping,
        parse_init_response,
    )
    from pigeon_protocol.feige_init import GET_MESSAGE_BY_INIT, _load_init_bytes, _patch_init_body
    from pigeon_protocol.foundation.chrome_hints import pigeon_im_headers
    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE
    from pigeon_protocol.pigeon_im import build_pigeon_im_url
    from pigeon_protocol.session import load_session
    from curl_cffi import requests as curl_requests

    session = load_session()
    body = _patch_init_body(_load_init_bytes(), session)
    url = build_pigeon_im_url(session, GET_MESSAGE_BY_INIT, sign=True)
    resp = curl_requests.post(
        url,
        data=body,
        headers=pigeon_im_headers(session),
        impersonate=DEFAULT_CURL_IMPERSONATE,
        timeout=20,
    )
    raw = resp.content or b""
    parsed = parse_init_response(raw)
    mapping = build_session_init_mapping(session, raw)
    bundle_path = export_init_mapping(session, raw)

    report = {
        "status": resp.status_code,
        "body_len": len(raw),
        "parsed_summary": {
            "inners": len(parsed.inners),
            "init_sync": [m.equiv_name for m in parsed.inners if m.role == "INIT_SYNC"],
            "send_usable": [m.equiv_name for m in parsed.inners if m.send_usable],
            "ws_send_frames": parsed.ws_send_frames,
            "ms4w_count": len(parsed.ms4w_tickets),
        },
        "missing_send_classes": mapping.get("missing_send_classes"),
        "bundle_export": str(bundle_path),
        "mapping": mapping,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if resp.status_code == 200 and parsed.inners else 1


if __name__ == "__main__":
    raise SystemExit(main())
