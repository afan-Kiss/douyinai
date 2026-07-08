"""HTTP-only conversation meta for Rust SDK frontier send (ticket + short_id)."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("pigeon.rust_sdk_conv_meta")

MS4W_TICKET_RE = re.compile(rb"MS4w[A-Za-z0-9+/=_-]{20,220}")
LONG_ID_RE = re.compile(rb"\d{16,20}")


def _device_id(session) -> str:
    return str(session.device_id or session.cookies.get("PIGEON_CID") or "")


def _scan_init_binary(data: bytes, *, device_id: str) -> dict[str, str]:
    out: dict[str, str] = {}
    ticket_m = MS4W_TICKET_RE.search(data)
    if ticket_m:
        out["ticket"] = ticket_m.group(0).decode("ascii", errors="ignore")
    candidates: list[str] = []
    for raw in LONG_ID_RE.findall(data):
        val = raw.decode("ascii", errors="ignore")
        if val == device_id:
            continue
        if val not in candidates:
            candidates.append(val)
    if candidates:
        # Prefer 19-digit conversation short ids over timestamps.
        candidates.sort(key=lambda x: (len(x) != 19, x))
        out["short_id"] = candidates[0]
    return out


def _load_init_bytes(session) -> tuple[bytes, str]:
    from pigeon_protocol.pure_config import STANDALONE_BUNDLE

    paths: list[tuple[Path, str]] = [
        (STANDALONE_BUNDLE / "get_message_by_init_response.bin", "bundle_init"),
    ]
    for path, label in paths:
        if path.is_file() and path.stat().st_size > 200:
            return path.read_bytes(), label

    try:
        from pigeon_protocol.feige_init import _post_get_message_by_init

        _post_get_message_by_init(session)
        path = STANDALONE_BUNDLE / "get_message_by_init_response.bin"
        if path.is_file():
            return path.read_bytes(), "init_http"
    except Exception as exc:
        logger.debug("init fetch for conv meta: %s", exc)
    return b"", "missing"


def resolve_conv_sdk_meta(session, *, conversation_id: str = "") -> dict[str, Any]:
    """
    Resolve IM ticket + conversation short_id for Rust SDK cloud/frontier send.

    Sources: env override → get_message_by_init binary (MS4w ticket + short_id).
    """
    device_id = _device_id(session)
    report: dict[str, Any] = {"device_id": device_id, "conversation_id": conversation_id or None}

    short_id = os.environ.get("PIGEON_CONV_SHORT_ID", "").strip()
    ticket = os.environ.get("PIGEON_CONV_TICKET", "").strip()
    if short_id:
        report["short_id_via"] = "env"
    if ticket:
        report["ticket_via"] = "env"

    raw, src = _load_init_bytes(session)
    report["init_source"] = src
    report["init_bytes"] = len(raw)
    if raw:
        scanned = _scan_init_binary(raw, device_id=device_id)
        if not short_id and scanned.get("short_id"):
            short_id = scanned["short_id"]
            report["short_id_via"] = f"init_scan/{src}"
        if not ticket and scanned.get("ticket"):
            ticket = scanned["ticket"]
            report["ticket_via"] = f"init_scan/{src}"

    report["short_id"] = short_id or None
    report["ticket"] = (ticket[:48] + "...") if ticket and len(ticket) > 52 else ticket
    report["has_ticket"] = bool(ticket)
    report["has_short_id"] = bool(short_id)
    report["ok"] = bool(short_id)
    report["_ticket_full"] = ticket
    report["_short_id_full"] = short_id
    return report
