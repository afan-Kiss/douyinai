#!/usr/bin/env python3
"""Probe xundan_chat_list — diagnose 11001 WhaleBlock."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.conv_list import _unsigned_url, list_conversations_relay
from pigeon_protocol.foundation.relay_client import BackstageRelayClient
from pigeon_protocol.session import load_session
from pigeon_protocol.whale_version import resolve_whale_versions


def _decode_msg(msg: str) -> str:
    if not msg:
        return ""
    if any(ord(c) > 127 for c in msg):
        return msg
    try:
        return msg.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return msg


def main() -> int:
    session = load_session()
    client = BackstageRelayClient(session)
    vers = resolve_whale_versions(session=session)

    report: dict = {
        "whale_versions": vers,
        "verifyFp": session.query_tokens.get("verifyFp") or session.cookies.get("s_v_web_id"),
        "unsigned_sample": _unsigned_url(queue_key="no_pay", page_size=20, session=session),
        "attempts": [],
    }

    for queue_key in ("no_pay", "no_order", "all"):
        unsigned = _unsigned_url(queue_key=queue_key, page_size=20, session=session)
        relay = client.get(unsigned, via=f"probe/{queue_key}")
        d = relay.data or {}
        inner = d.get("data")
        n = 0
        if isinstance(inner, dict):
            ul = inner.get("user_list") or []
            n = len(ul) if isinstance(ul, list) else 0
        report["attempts"].append(
            {
                "queue_key": queue_key,
                "code": relay.api_code(),
                "items": n,
                "msg": _decode_msg(str(d.get("msg") or "")),
                "sign_via": relay.sign.via if relay.sign else None,
                "relay_ok": relay.ok,
            }
        )

    merged = list_conversations_relay(session, size=20, queue_keys=("no_pay",))
    report["list_conversations_relay"] = {
        "ok": merged.get("ok"),
        "error": merged.get("error"),
        "api_code": merged.get("api_code"),
        "items": len(merged.get("items") or []),
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if any(a.get("items") for a in report["attempts"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
