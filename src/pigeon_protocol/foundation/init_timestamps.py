"""Parse get_message_by_init HTTP response timestamps (field 10/11)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pigeon_protocol.foundation.init_inner_mapper import walk_init_protobuf
from pigeon_protocol.pure_config import STANDALONE_BUNDLE


def parse_init_timestamps(raw: bytes) -> dict[str, int]:
    """Return init protobuf field 10/11 (microsecond-scale server timestamps)."""
    out = {"ts_start": 0, "ts_end": 0, "span": 0}
    if not raw:
        return out
    for row in walk_init_protobuf(raw):
        fn = int(row.get("field") or 0)
        if fn == 10:
            out["ts_start"] = int(row.get("varint") or 0)
        elif fn == 11:
            out["ts_end"] = int(row.get("varint") or 0)
    if out["ts_start"] and out["ts_end"]:
        out["span"] = out["ts_end"] - out["ts_start"]
    return out


def load_init_bytes(session=None) -> tuple[bytes, str]:
    """Load init response body from bundle or live HTTP."""
    path = STANDALONE_BUNDLE / "get_message_by_init_response.bin"
    if path.is_file() and path.stat().st_size > 200:
        return path.read_bytes(), "bundle"

    if session is not None:
        try:
            from pigeon_protocol.feige_init import _post_get_message_by_init

            _post_get_message_by_init(session)
            if path.is_file():
                return path.read_bytes(), "init_http"
        except Exception:
            pass
    return b"", "missing"


def resolve_edbx_timestamp_us(session=None, *, raw: bytes | None = None) -> tuple[int, str]:
    """
    Pick field-12 microsecond timestamp for edbX prefix.

    Priority: env override → init field 10 → wall clock (µs).
    """
    import os

    env = os.environ.get("PIGEON_EDBX_TS_US", "").strip()
    if env.isdigit():
        return int(env), "env"

    if raw is None and session is not None:
        raw, _ = load_init_bytes(session)
    if raw:
        ts = parse_init_timestamps(raw)
        if ts.get("ts_start"):
            return int(ts["ts_start"]), "init_field10"

    return time.time_ns() // 1000, "wall_us"
