"""Fetch x-secsdk-csrf-token via HEAD (secsdk 1.2.22 protocol) — no CDP."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("pigeon.secsdk_csrf")

CACHE_TTL_SEC = 3600

ORDER_HEAD_PATH = "/backstage/cmpoent/order/query"


def _analysis_env() -> Path:
    from pigeon_protocol.account_context import analysis_env_file

    return analysis_env_file()


def _bundle_env() -> Path:
    from pigeon_protocol.account_context import bundle_file

    return bundle_file("bdms_browser_env.json")


def _order_snapshot() -> Path:
    from pigeon_protocol.account_context import bundle_file

    return bundle_file("order_sign_snapshot.json")


def __getattr__(name: str):
    if name == "ENV_FILE":
        return _analysis_env()
    if name == "BUNDLE_ENV":
        return _bundle_env()
    if name == "SNAPSHOT_FILE":
        return _order_snapshot()
    raise AttributeError(name)


def _parse_ware_csrf(raw: str) -> dict[str, str]:
    """Parse x-ware-csrf-token: status,token,maxAge,message,sessionId"""
    parts = [p.strip() for p in str(raw or "").split(",")]
    if len(parts) < 2 or parts[0] != "0":
        raise RuntimeError(f"csrf fetch failed: {raw[:120]}")
    token = parts[1]
    session_id = parts[4] if len(parts) > 4 else ""
    return {"token": token, "session_id": session_id, "max_age": parts[2] if len(parts) > 2 else ""}


def build_csrf_header(token: str, session_id: str) -> str:
    return f"{token},{session_id}"


def fetch_csrf_via_head(session, *, path: str = ORDER_HEAD_PATH) -> str:
    """HEAD pigeon → x-ware-csrf-token → x-secsdk-csrf-token value."""
    from pigeon_protocol.config import IM_HOST, PIGEON_HOST
    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE, DEFAULT_USER_AGENT
    from pigeon_protocol.http_transport import curl_cffi_available

    if not curl_cffi_available():
        raise RuntimeError("curl_cffi required for csrf HEAD fetch")

    from curl_cffi import requests as curl_requests

    cookie = session.cookie_header()
    if not cookie:
        raise RuntimeError("session has no cookies for csrf fetch")

    url = f"{PIGEON_HOST}{path}"
    hdr = {
        "User-Agent": session.user_agent or DEFAULT_USER_AGENT,
        "Referer": f"{IM_HOST}/pc_seller_v2/main/workspace",
        "Origin": IM_HOST,
        "x-secsdk-csrf-request": "1",
        "x-secsdk-csrf-version": "1.2.22",
        "Cookie": cookie,
    }
    resp = curl_requests.head(
        url,
        headers=hdr,
        impersonate=DEFAULT_CURL_IMPERSONATE,
        timeout=15,
        allow_redirects=True,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"csrf HEAD status={resp.status_code}")

    ware = resp.headers.get("x-ware-csrf-token") or resp.headers.get("X-Ware-Csrf-Token")
    if not ware:
        raise RuntimeError("missing x-ware-csrf-token in HEAD response")

    parsed = _parse_ware_csrf(ware)
    session_id = session.cookies.get("csrf_session_id") or parsed["session_id"]
    if not session_id:
        raise RuntimeError("no csrf_session_id cookie and server returned empty sessionId")
    return build_csrf_header(parsed["token"], session_id)


def _load_env_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_env_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_relay_template(max_age_sec: int = 86400 * 7) -> dict[str, str]:
    order_snap = _order_snapshot()
    for path in (order_snap, _analysis_env(), _bundle_env()):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            hdr = data.get("headers") if path == order_snap else data.get("relayHeaders")
            if not isinstance(hdr, dict):
                continue
            ts = int(data.get("relayHeadersTs") or data.get("ts") or 0)
            if ts and time.time() - ts > max_age_sec:
                continue
            return {k: str(v) for k, v in hdr.items() if k.lower() not in {"content-length", "host"}}
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def refresh_relay_headers(session, *, persist: bool = True) -> dict[str, str]:
    """Refresh CSRF via HEAD; merge into existing relay template if present."""
    from pigeon_protocol.http_client import BackstageHttpClient
    from pigeon_protocol.config import IM_HOST

    csrf = fetch_csrf_via_head(session)
    template = _load_relay_template(max_age_sec=86400 * 7)

    if template:
        hdr = dict(template)
    else:
        from pigeon_protocol.foundation.chrome_hints import backstage_fetch_headers

        hdr = backstage_fetch_headers(session, method="POST")

    hdr["Cookie"] = session.cookie_header()
    hdr["content-type"] = "application/json;charset=UTF-8"
    hdr["x-secsdk-csrf-token"] = csrf
    # drop duplicate casing variants
    for dup in ("Content-Type", "Referer", "User-Agent"):
        if dup in hdr and dup.lower() in hdr:
            hdr.pop(dup, None)

    if persist:
        for env_path in (_analysis_env(), _bundle_env()):
            env = _load_env_file(env_path)
            env["csrfHeader"] = csrf
            env["csrfToken"] = session.cookies.get("csrf_session_id") or ""
            env["relayHeaders"] = {k: v for k, v in hdr.items()}
            env["relayHeadersTs"] = int(time.time())
            _save_env_file(env_path, env)
        logger.info("persisted relayHeaders csrf=%s…", csrf[:48])

    return hdr


def get_csrf_header(session, *, force_refresh: bool = False) -> str:
    """Return csrf header value, using cache if fresh."""
    if not force_refresh:
        for path in (_analysis_env(), _bundle_env()):
            env = _load_env_file(path)
            ts = int(env.get("relayHeadersTs") or 0)
            csrf = env.get("csrfHeader")
            if csrf and ts and time.time() - ts < CACHE_TTL_SEC:
                return str(csrf)
    return fetch_csrf_via_head(session)
