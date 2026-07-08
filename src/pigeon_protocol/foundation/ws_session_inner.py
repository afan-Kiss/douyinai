"""Per-session 169B inner blob cache — reuse within session without re-harvest."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("pigeon.ws_session_inner")

ROOT = Path(__file__).resolve().parents[3]
CACHE_FILE = ROOT / "session" / "ws_inner_cache.json"


def refresh_paths() -> None:
    global CACHE_FILE
    from pigeon_protocol.account_context import inner_cache_file

    CACHE_FILE = inner_cache_file()


def _session_key(session) -> str:
    sid = str(session.cookies.get("sessionid") or session.cookies.get("sid_tt") or "")
    cid = str(session.cookies.get("PIGEON_CID") or session.device_id or "")
    raw = f"{sid}:{cid}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _inner_fp(inner: bytes) -> str:
    return hashlib.sha256(inner).hexdigest()[:16]


def _load_cache() -> dict[str, Any]:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(data: dict[str, Any]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def inner_fp_for_text(text: str) -> str | None:
    from pigeon_protocol.foundation.ws_blob_compute import inner_class_for_text_b

    bl = len(text.encode("utf-8"))
    ic = inner_class_for_text_b(bl)
    if ic:
        return ic.class_id
    from pigeon_protocol.ws_sign_bucket import bucket_for_text_len

    spec = bucket_for_text_len(bl)
    if spec:
        return f"spec_{spec.name}"
    return None


def get_cached_inner(session, text: str) -> bytes | None:
    """Return session-scoped 169B inner for this text's fingerprint group."""
    fp = inner_fp_for_text(text)
    if not fp:
        return None
    key = _session_key(session)
    entry = _load_cache().get(key, {})
    hex_inner = entry.get(fp)
    if not hex_inner:
        return None
    try:
        inner = bytes.fromhex(hex_inner)
        return inner if len(inner) == 169 else None
    except ValueError:
        return None


def store_inner_from_frame(session, frame: bytes, text: str) -> bool:
    """Cache 169B inner from a successfully built/sent frame."""
    from pigeon_protocol.ws_sign import locate_signature_region
    from pigeon_protocol.ws_sign_decode import decode_blob

    fp = inner_fp_for_text(text)
    if not fp:
        return False
    region = locate_signature_region(frame)
    if not region:
        return False
    try:
        inner = decode_blob(region.blob)
    except Exception:
        return False
    if len(inner) != 169:
        return False

    try:
        from pigeon_protocol.foundation.ws_inner_edbx import is_edbx_inner, store_envelope_template

        if is_edbx_inner(inner):
            store_envelope_template(session, inner, source="ws_send")
            try:
                from pigeon_protocol.session_portable import sync_portable_inner_sidecar

                sync_portable_inner_sidecar(session, force=True)
            except Exception as exc:
                logger.debug("sidecar after ws send: %s", exc)
    except Exception as exc:
        logger.debug("edbx envelope store: %s", exc)

    cache = _load_cache()
    key = _session_key(session)
    entry = cache.setdefault(key, {})
    entry[fp] = inner.hex()
    entry["_meta"] = {"inner_fp": _inner_fp(inner), "textB": len(text.encode("utf-8"))}
    cache[key] = entry
    _save_cache(cache)
    logger.debug("cached inner fp=%s session=%s", fp, key[:12])
    return True


def session_has_inner_cache(session) -> bool:
    key = _session_key(session)
    entry = _load_cache().get(key, {})
    return bool([k for k in entry if not k.startswith("_")])
