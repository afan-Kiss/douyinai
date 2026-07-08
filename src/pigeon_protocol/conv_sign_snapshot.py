"""Bootstrap xundan sign snapshot — CDP/curl_relay capture for offline curl_cffi replay."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("pigeon.conv_sign_snapshot")

DEFAULT_MAX_AGE_SEC = int(os.getenv("PIGEON_CONV_SNAPSHOT_TTL", "7200"))


def _bundle_dir() -> Path:
    from pigeon_protocol.account_context import bundle_dir

    return bundle_dir()


def _snapshot_path() -> Path:
    from pigeon_protocol.account_context import bundle_file

    return bundle_file("conv_sign_snapshot.json")


def __getattr__(name: str) -> Path:
    if name == "BUNDLE_ROOT":
        return _bundle_dir()
    if name == "SNAPSHOT_FILE":
        return _snapshot_path()
    raise AttributeError(name)


def _now() -> int:
    return int(time.time())


def load_snapshot_doc() -> dict[str, Any] | None:
    path = _snapshot_path()
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def snapshot_age_sec(doc: dict[str, Any] | None = None) -> int | None:
    doc = doc or load_snapshot_doc()
    if not doc or not doc.get("ts"):
        return None
    return max(0, _now() - int(doc["ts"]))


def has_fresh_snapshot(*, max_age_sec: int | None = None) -> bool:
    doc = load_snapshot_doc()
    if not doc or not isinstance(doc.get("queues"), dict):
        return False
    age = snapshot_age_sec(doc)
    if age is None:
        return False
    ttl = max_age_sec if max_age_sec is not None else DEFAULT_MAX_AGE_SEC
    return age <= ttl and bool(doc["queues"])


def save_queue_snapshot(
    *,
    queue_key: str,
    url: str,
    headers: dict[str, str],
    page_size: int = 20,
    source: str = "curl_relay/bootstrap",
    unsigned_url: str = "",
) -> Path:
    """Merge one queue_key entry into per-account bundle/conv_sign_snapshot.json."""
    path = _snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = load_snapshot_doc() or {"ts": _now(), "queues": {}, "source": source}
    queues = doc.setdefault("queues", {})
    if not isinstance(queues, dict):
        queues = {}
        doc["queues"] = queues

    clean_hdr = {
        str(k): str(v)
        for k, v in (headers or {}).items()
        if k.lower() not in ("content-length", "host", ":authority", ":method", ":path", ":scheme")
    }
    entry: dict[str, Any] = {
        "url": str(url),
        "headers": clean_hdr,
        "page_size": int(page_size),
        "ts": _now(),
    }
    if unsigned_url:
        entry["unsigned_url"] = str(unsigned_url)
    queues[str(queue_key)] = entry
    doc["ts"] = _now()
    doc["source"] = source
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _invalidate_queue(queue_key: str) -> None:
    path = _snapshot_path()
    doc = load_snapshot_doc()
    if not doc or not isinstance(doc.get("queues"), dict):
        return
    if queue_key in doc["queues"]:
        doc["queues"].pop(queue_key, None)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_xundan_via_snapshot(
    session,
    *,
    queue_key: str = "no_order",
    page_size: int = 20,
    max_age_sec: int | None = None,
) -> dict[str, Any] | None:
    """Replay xundan GET — re-sign unsigned URL when stored, else frozen signed URL."""
    doc = load_snapshot_doc()
    if not doc:
        return None
    queues = doc.get("queues")
    if not isinstance(queues, dict):
        return None

    entry = queues.get(queue_key)
    if not isinstance(entry, dict) or not entry.get("url"):
        return None

    age = snapshot_age_sec(doc)
    ttl = max_age_sec if max_age_sec is not None else DEFAULT_MAX_AGE_SEC
    if age is not None and age > ttl:
        return None

    from pigeon_protocol.conv_list import _unsigned_url, parse_conversation_items
    from pigeon_protocol.foundation.bdms_sign import persist_tokens_to_session, sign_backstage_url
    from pigeon_protocol.http_client import DEFAULT_CURL_IMPERSONATE
    from pigeon_protocol.http_transport import curl_cffi_available, request_json
    from pigeon_protocol.order_relay_headers import build_order_relay_headers

    if not curl_cffi_available():
        return None

    unsigned = str(entry.get("unsigned_url") or "") or _unsigned_url(
        queue_key=queue_key, page_size=page_size, session=session
    )
    sign = sign_backstage_url(unsigned, method="GET")
    if sign.ok:
        persist_tokens_to_session(session, sign)
        fetch_url = sign.signed_url
        via_tag = "conv_snapshot/resign"
    else:
        fetch_url = str(entry["url"])
        via_tag = "conv_snapshot/frozen"

    hdr = dict(entry.get("headers") or {})
    live = build_order_relay_headers(session, for_method="GET")
    for key in ("User-Agent", "Referer", "Origin", "x-secsdk-csrf-token", "X-IM-PC-Version"):
        lk = key if key in live else key.lower()
        if live.get(lk):
            hdr[key if key.startswith("x-") or key.startswith("X-") else lk] = live[lk]
    cookie = session.cookie_header()
    if cookie:
        hdr["Cookie"] = cookie

    im_ver = str(session.query_tokens.get("im_pc_version") or "")
    if im_ver:
        hdr["X-IM-PC-Version"] = im_ver

    raw = request_json(
        "GET",
        fetch_url,
        headers=hdr,
        transport="curl_cffi",
        impersonate=DEFAULT_CURL_IMPERSONATE,
    )
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    items = parse_conversation_items({"data": data})
    code = data.get("code") if isinstance(data, dict) else None
    if str(code) == "11001":
        _invalidate_queue(queue_key)
        return None
    return {
        "ok": bool(items) or str(code) in ("0", "0.0"),
        "via": via_tag,
        "queue_key": queue_key,
        "api_code": code,
        "items": items,
        "data": data,
        "url": fetch_url,
        "snapshot_age_sec": age,
    }


def refresh_snapshots_from_cdp(session, *, queue_keys: tuple[str, ...] | None = None, page_size: int = 20) -> dict[str, Any]:
    """Bootstrap conv snapshots via CDP sign+curl when Chrome is logged in."""
    from pigeon_protocol.config import XUNDAN_QUEUE_KEYS
    from pigeon_protocol.conv_xundan_curl_relay import fetch_xundan_via_curl_relay

    keys = queue_keys or XUNDAN_QUEUE_KEYS
    report: dict[str, Any] = {"queues": {}, "saved": []}
    for qk in keys:
        try:
            curl = fetch_xundan_via_curl_relay(session, queue_key=qk, page_size=page_size)
        except Exception as exc:
            report["queues"][qk] = {"ok": False, "error": str(exc)}
            continue
        report["queues"][qk] = {
            "ok": curl.get("ok"),
            "code": curl.get("api_code"),
            "items": len(curl.get("items") or []),
        }
        if curl.get("ok") and curl.get("url"):
            report["saved"].append(qk)
    report["ok"] = bool(report["saved"])
    snap = _snapshot_path()
    report["snapshot"] = str(snap) if snap.is_file() else ""
    return report
