#!/usr/bin/env python3
"""Find deskVersion / X-IM-PC-Version for whale-protected backstage APIs."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.config import PIGEON_HOST
from pigeon_protocol.feige_init import _fetch_workspace_html
from pigeon_protocol.foundation.relay_client import BackstageRelayClient
from pigeon_protocol.order_relay_headers import build_order_relay_headers
from pigeon_protocol.foundation.bdms_sign import sign_backstage_url, persist_tokens_to_session
from pigeon_protocol.http_transport import request_json
from pigeon_protocol.session import load_session
from urllib.parse import urlencode


def _candidates(html: str) -> list[str]:
    out: list[str] = []
    for m in re.finditer(r"1\.0\.1\.\d{3,5}", html or ""):
        v = m.group(0)
        if v not in out:
            out.append(v)
    for m in re.finditer(r'deskVersion["\']?\s*[:=]\s*["\']([^"\']+)', html or "", re.I):
        if m.group(1) not in out:
            out.append(m.group(1))
    tpl = ROOT / "standalone_bundle" / "bdms_browser_env.json"
    if tpl.is_file():
        try:
            data = json.loads(tpl.read_text(encoding="utf-8"))
            cv = (data.get("convListTemplate") or {}).get("_v")
            if cv and cv not in out:
                out.append(str(cv))
        except OSError:
            pass
    out.extend(["1.0.1.7626", "1.0.1.6225", "1.0.1.6174"])
    # dedupe preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for v in out:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def _try_conv(session, *, desk_version: str) -> dict:
    params = {
        "biz_type": "4",
        "PIGEON_BIZ_TYPE": "2",
        "_pms": "1",
        "device_platform": "web",
        "FUSION": "true",
        "_v": desk_version,
        "queue_key": "no_pay",
        "page_size": "20",
    }
    unsigned = f"{PIGEON_HOST}/backstage/workstation/xundan_chat_list?{urlencode(params)}"
    sign = sign_backstage_url(unsigned, method="GET")
    persist_tokens_to_session(session, sign)
    hdr = build_order_relay_headers(session, for_method="GET")
    hdr["X-IM-PC-Version"] = desk_version
    raw = request_json("GET", sign.signed_url, headers=hdr, transport="curl_cffi")
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    ul = inner.get("user_list") if isinstance(inner, dict) else []
    n = len(ul) if isinstance(ul, list) else 0
    msg = str(data.get("msg") or "")
    try:
        msg = msg.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    return {"desk_version": desk_version, "code": data.get("code"), "items": n, "msg": msg[:80]}


def main() -> int:
    session = load_session()
    html = _fetch_workspace_html(session)
    report = {"html_len": len(html), "attempts": []}
    for ver in _candidates(html):
        report["attempts"].append(_try_conv(session, desk_version=ver))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = any(a.get("code") == 0 and a.get("items", 0) > 0 for a in report["attempts"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
