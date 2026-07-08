"""Headers for curl_cffi order relay — live CSRF + chrome hints (no HAR at runtime)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("pigeon.relay_headers")

_DROP = frozenset(
    {
        "content-length",
        "host",
        ":authority",
        ":method",
        ":path",
        ":scheme",
        "x-secsdk-csrf-token",  # always refreshed live
    }
)


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


def _read_template() -> dict[str, str]:
    from pigeon_protocol.pure_config import relay_headers_from_hints

    if relay_headers_from_hints():
        return {}
    order_snap = _order_snapshot()
    for path in (_analysis_env(), _bundle_env(), order_snap):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            hdr = data.get("relayHeaders") if path != order_snap else data.get("headers")
            if isinstance(hdr, dict) and hdr:
                return {k: str(v) for k, v in hdr.items() if k.lower() not in _DROP}
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def load_relay_header_template(**_) -> dict[str, str]:
    """Cached chrome-hints template (csrf excluded — fetched live)."""
    from pigeon_protocol.pure_config import relay_headers_from_hints

    if relay_headers_from_hints():
        return {"_via": "chrome_hints"}
    return _read_template()


def build_order_relay_headers(session, *, cookie: str | None = None, force_refresh: bool = False, for_method: str = "POST") -> dict[str, str]:
    """Chrome hints from session UA + live CSRF HEAD fetch."""
    from pigeon_protocol.foundation.chrome_hints import backstage_fetch_headers
    from pigeon_protocol.secsdk_csrf import fetch_csrf_via_head

    template = _read_template()
    ck = cookie or session.cookie_header()

    if template:
        hdr = dict(template)
    else:
        hdr = backstage_fetch_headers(session, method=for_method.upper())

    if ck:
        hdr["Cookie"] = ck
    if for_method.upper() != "GET":
        hdr["content-type"] = "application/json;charset=UTF-8"
    else:
        hdr.pop("content-type", None)
        hdr.pop("Content-Type", None)
        hdr.setdefault("accept", "application/json, text/plain, */*")
        hdr.setdefault("accept-language", "zh-CN,zh;q=0.9")
        hdr.setdefault("cache-control", "no-cache")
        hdr.setdefault("pragma", "no-cache")
        hdr.setdefault("sec-fetch-dest", "empty")
        hdr.setdefault("sec-fetch-mode", "cors")
        hdr.setdefault("sec-fetch-site", "same-site")

    try:
        hdr["x-secsdk-csrf-token"] = fetch_csrf_via_head(session)
    except Exception as exc:
        logger.warning("live csrf HEAD failed: %s", exc)
        if force_refresh:
            raise
        from pigeon_protocol.pure_config import relay_headers_from_hints

        if relay_headers_from_hints():
            raise
        for path in (_analysis_env(), _bundle_env(), _order_snapshot()):
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                csrf = data.get("csrfHeader") or (data.get("headers") or {}).get("x-secsdk-csrf-token")
                if csrf:
                    hdr["x-secsdk-csrf-token"] = str(csrf)
                    break
            except (OSError, json.JSONDecodeError):
                continue

    for dup in ("Content-Type", "Referer", "User-Agent"):
        if dup in hdr and dup.lower() in hdr:
            hdr.pop(dup, None)
    return hdr
