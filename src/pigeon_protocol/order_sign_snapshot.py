"""Bootstrap sign snapshot — curl_relay capture for offline curl_cffi replay."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _bundle_dir() -> Path:
    from pigeon_protocol.account_context import bundle_dir

    return bundle_dir()


def _snapshot_path() -> Path:
    from pigeon_protocol.account_context import bundle_file

    return bundle_file("order_sign_snapshot.json")


def __getattr__(name: str) -> Path:
    if name == "BUNDLE_ROOT":
        return _bundle_dir()
    if name == "SNAPSHOT_FILE":
        return _snapshot_path()
    raise AttributeError(name)


def save_sign_snapshot(
    *,
    url: str,
    headers: dict[str, str],
    sample_body: dict[str, Any],
    source: str = "curl_relay/bootstrap",
) -> Path:
    path = _snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": int(time.time()),
        "source": source,
        "url": url,
        "headers": headers,
        "sample_body": sample_body,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_sign_snapshot() -> dict[str, Any] | None:
    path = _snapshot_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def query_orders_via_snapshot(session, security_user_id: str) -> dict[str, Any] | None:
    """Replay order query using snapshot chrome hints + fresh bdms sign."""
    snap = load_sign_snapshot()
    if not snap or not snap.get("headers"):
        return None

    from pigeon_protocol.config import ORDER_QUERY_PATH, PIGEON_HOST
    from pigeon_protocol.foundation.bdms_sign import sign_backstage_url, persist_tokens_to_session
    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE
    from pigeon_protocol.http_transport import curl_cffi_available, order_api_ok, request_json
    from pigeon_protocol.order_relay_headers import build_order_relay_headers
    from pigeon_protocol.session import save_session
    from pigeon_protocol.whale_params import backstage_query_base

    if not curl_cffi_available():
        return None

    body = {
        "security_user_id": security_user_id,
        "page_no": 0,
        "page_size": 5,
        "search_words": "",
        "is_init_tab": 0,
        "tab_type": 1,
        "biz_type": 2,
        "open_params": {},
        "workstation_opt_version": "v2",
        "service_entity_id": "",
        "version": "1.0",
        "workstation_opt_gray": True,
    }

    unsigned = f"{PIGEON_HOST}{ORDER_QUERY_PATH}?{backstage_query_base(session=session)}"
    sign = sign_backstage_url(unsigned, method="POST", body=body)
    if not sign.ok:
        return None
    persist_tokens_to_session(session, sign)
    try:
        save_session(session)
    except OSError:
        pass

    hdr = build_order_relay_headers(session, for_method="POST")
    for k, v in (snap.get("headers") or {}).items():
        lk = str(k).lower()
        if lk in ("cookie", "x-secsdk-csrf-token", "content-length", "host"):
            continue
        hdr.setdefault(k, str(v))

    raw = request_json(
        "POST",
        sign.signed_url,
        headers=hdr,
        json_body=body,
        transport="curl_cffi",
        impersonate=DEFAULT_CURL_IMPERSONATE,
    )
    if not order_api_ok(raw):
        hdr = build_order_relay_headers(session, force_refresh=True, for_method="POST")
        sign = sign_backstage_url(unsigned, method="POST", body=body)
        if sign.ok:
            persist_tokens_to_session(session, sign)
            raw = request_json(
                "POST",
                sign.signed_url,
                headers=hdr,
                json_body=body,
                transport="curl_cffi",
                impersonate=DEFAULT_CURL_IMPERSONATE,
            )
    raw["via"] = "snapshot/curl_cffi"
    raw["snapshot_age_sec"] = int(time.time()) - int(snap.get("ts") or 0)
    return raw
