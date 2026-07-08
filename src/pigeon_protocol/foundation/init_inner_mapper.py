"""Map get_message_by_init response inners → A–G send equivalence classes."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pigeon_protocol.foundation.ws_blob_compute import classify_inner, inner_class_registry
from pigeon_protocol.foundation.ws_inner_bootstrap import scan_binary_for_inners
from pigeon_protocol.parsers.ws_frame_builder import read_varint
from pigeon_protocol.pure_config import STANDALONE_BUNDLE
from pigeon_protocol.ws_inner_buckets import INIT_SYNC_INNER_FP, classify_inner_bucket

logger = logging.getLogger("pigeon.init_inner_mapper")

INIT_SYNC_KEY = "__init_sync__"
MS4W_RE = re.compile(rb"MS4w[A-Za-z0-9+/=_\-]{16,512}")

# Init sync inner: no A–G bucket header; role is inbox/data-sync seed, not WS text-send.
INIT_SYNC_HEADER = (INIT_SYNC_INNER_FP["le32_0"], INIT_SYNC_INNER_FP["le32_4"])


@dataclass
class InnerMapping:
    class_id: str
    equiv_name: str
    role: str
    send_usable: bool
    header_hex: str
    bucket: str | None
    offset: int = 0
    from_frame: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.class_id,
            "equiv_name": self.equiv_name,
            "role": self.role,
            "send_usable": self.send_usable,
            "header_hex": self.header_hex,
            "bucket": self.bucket,
            "offset": self.offset,
            "from_frame": self.from_frame,
        }


@dataclass
class InitParseResult:
    body_len: int = 0
    fields: list[dict[str, Any]] = field(default_factory=list)
    inners: list[InnerMapping] = field(default_factory=list)
    ms4w_tickets: list[str] = field(default_factory=list)
    has_client_message_id: bool = False
    ws_send_frames: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "body_len": self.body_len,
            "fields": self.fields,
            "inners": [i.to_dict() for i in self.inners],
            "ms4w_tickets": self.ms4w_tickets,
            "has_client_message_id": self.has_client_message_id,
            "ws_send_frames": self.ws_send_frames,
            "send_class_count": sum(1 for i in self.inners if i.send_usable),
            "init_sync_count": sum(1 for i in self.inners if i.role == "INIT_SYNC"),
        }


def _header_pair(inner: bytes) -> tuple[int, int]:
    if len(inner) < 8:
        return (0, 0)
    return (
        int.from_bytes(inner[0:4], "little"),
        int.from_bytes(inner[4:8], "little"),
    )


def map_inner_to_equiv_class(
    inner: bytes,
    *,
    offset: int = 0,
    from_frame: bool = False,
    context: str = "",
) -> InnerMapping:
    """
    Classify a 169B inner against send registry (A–G) or INIT_SYNC role.

    Init response carries one inbox-sync inner (no A–G bucket header); it must
    not be used for WS text-send — only session-scoped send classes apply there.
    """
    if len(inner) != 169:
        raise ValueError(f"expected 169 bytes, got {len(inner)}")

    layout = classify_inner(inner)
    class_id = str(layout.get("sha256_prefix") or hashlib.sha256(inner).hexdigest()[:16])
    header_hex = str(layout.get("header_hex") or inner[:8].hex())
    bucket = layout.get("bucket")

    registry = inner_class_registry()
    send_class = registry.get(class_id)
    if send_class:
        return InnerMapping(
            class_id=class_id,
            equiv_name=send_class.name,
            role="SEND",
            send_usable=True,
            header_hex=header_hex,
            bucket=bucket or send_class.name,
            offset=offset,
            from_frame=from_frame,
        )

    if bucket:
        return InnerMapping(
            class_id=class_id,
            equiv_name=bucket,
            role="SEND",
            send_usable=True,
            header_hex=header_hex,
            bucket=bucket,
            offset=offset,
            from_frame=from_frame,
        )

    hdr = _header_pair(inner)
    if hdr == INIT_SYNC_HEADER or context == "init_top_level":
        equiv = "INIT_SYNC"
    elif classify_inner_bucket(inner) is None and not from_frame:
        equiv = "INIT_SYNC"
    else:
        equiv = "UNKNOWN"

    return InnerMapping(
        class_id=class_id,
        equiv_name=equiv,
        role=equiv,
        send_usable=False,
        header_hex=header_hex,
        bucket=None,
        offset=offset,
        from_frame=from_frame,
    )


def walk_init_protobuf(raw: bytes, *, max_depth: int = 3) -> list[dict[str, Any]]:
    """Shallow protobuf field inventory for init response."""
    fields: list[dict[str, Any]] = []
    pos = 0
    while pos < len(raw):
        if pos >= len(raw):
            break
        tag = raw[pos]
        wire = tag & 7
        field_num = tag >> 3
        pos += 1
        if wire == 0:
            val, pos = read_varint(raw, pos)
            fields.append({"field": field_num, "wire": wire, "varint": val})
        elif wire == 2:
            length, val_start = read_varint(raw, pos)
            val_end = val_start + length
            if val_end > len(raw):
                break
            chunk = raw[val_start:val_end]
            entry: dict[str, Any] = {
                "field": field_num,
                "wire": wire,
                "len": length,
                "head_hex": chunk[:16].hex() if chunk else "",
            }
            if length < 256:
                try:
                    entry["ascii_preview"] = chunk.decode("utf-8", errors="replace")[:120]
                except Exception:
                    pass
            fields.append(entry)
            pos = val_end
        else:
            break
    return fields


def parse_init_response(raw: bytes) -> InitParseResult:
    """Parse init HTTP body: protobuf fields, 169B inners, IM tickets."""
    result = InitParseResult(body_len=len(raw))
    if not raw:
        return result

    result.fields = walk_init_protobuf(raw)
    result.has_client_message_id = b"s:client_message_id" in raw
    result.ms4w_tickets = sorted({m.group(0).decode("ascii", errors="ignore") for m in MS4W_RE.finditer(raw)})

    from pigeon_protocol.ws_sign import locate_signature_region

    if result.has_client_message_id:
        pos = 0
        while True:
            idx = raw.find(b"s:client_message_id", pos)
            if idx < 0:
                break
            chunk = raw[idx : idx + 5000]
            if locate_signature_region(chunk):
                result.ws_send_frames += 1
            pos = idx + 1

    for hit in scan_binary_for_inners(raw):
        inner = bytes.fromhex(hit["inner_hex"])
        ctx = "init_top_level" if not hit.get("from_frame") else "ws_frame"
        mapping = map_inner_to_equiv_class(
            inner,
            offset=int(hit.get("offset") or 0),
            from_frame=bool(hit.get("from_frame")),
            context=ctx,
        )
        result.inners.append(mapping)

    return result


def registry_send_table() -> list[dict[str, Any]]:
    """A–G send equivalence classes from harvest registry."""
    reg = inner_class_registry()
    rows: list[dict[str, Any]] = []
    for ic in sorted(reg.values(), key=lambda c: c.name):
        rows.append(
            {
                "equiv_name": ic.name,
                "class_id": ic.class_id,
                "header_hex": ic.header_bytes.hex(),
                "canonical_text_b": ic.canonical_text_b,
                "text_range": f"{min(ic.text_lengths)}-{max(ic.text_lengths)}",
                "role": "SEND",
                "send_usable": True,
            }
        )
    return rows


def build_session_init_mapping(session, raw: bytes | None = None) -> dict[str, Any]:
    """Full init→send mapping report for current session."""
    from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache
    from pigeon_protocol.foundation.ws_session_inner import _session_key

    if raw is None:
        raw = b""

    parsed = parse_init_response(raw) if raw else InitParseResult()
    sk = _session_key(session)
    cached = _load_session_class_cache(session)

    cached_mapped: list[dict[str, Any]] = []
    for cid, inner in cached.items():
        if cid == INIT_SYNC_KEY:
            continue
        try:
            m = map_inner_to_equiv_class(inner)
            cached_mapped.append(
                {
                    **m.to_dict(),
                    "in_session_cache": True,
                    "cache_key": cid,
                }
            )
        except ValueError:
            continue

    send_registry = registry_send_table()
    init_inners = [i.to_dict() for i in parsed.inners if i.role == "INIT_SYNC"]
    send_inners = [i.to_dict() for i in parsed.inners if i.send_usable]

    missing_send = [
        row["equiv_name"]
        for row in send_registry
        if row["class_id"] not in cached
        and row["class_id"] not in {i.class_id for i in parsed.inners if i.send_usable}
    ]

    return {
        "version": 1,
        "session_key": sk,
        "formula": "send: inner(textB)=session_constant[class(textB)]; init: INIT_SYNC seed only",
        "init_parse": parsed.to_dict() if raw else None,
        "init_sync_inners": init_inners,
        "send_inners_from_init": send_inners,
        "send_registry": send_registry,
        "session_cache_mapped": cached_mapped,
        "missing_send_classes": missing_send,
        "init_seeds_send": bool(send_inners),
        "cold_start_via_init_only": bool(send_inners) and not missing_send,
        "notes": (
            "Init response embeds one INIT_SYNC 169B inner (inbox sync), not A–G send blobs. "
            "Send classes require bundle export, prior harvest, or first in-session WS send."
        ),
    }


def store_init_sync_inner(session, inner: bytes, *, source: str = "init") -> str:
    """Persist init-only inner under dedicated cache key (not used for send)."""
    from pigeon_protocol.foundation.ws_blob_compute import _store_session_class_inner

    mapping = map_inner_to_equiv_class(inner, context="init_top_level")
    if mapping.send_usable:
        _store_session_class_inner(session, mapping.class_id, inner)
        return mapping.class_id

    from pigeon_protocol.foundation.ws_session_inner import _load_cache, _save_cache, _session_key

    cache = _load_cache()
    key = _session_key(session)
    entry = cache.setdefault(key, {})
    entry[INIT_SYNC_KEY] = inner.hex()
    entry.setdefault("_roles", {})[INIT_SYNC_KEY] = {
        "role": "INIT_SYNC",
        "class_id": mapping.class_id,
        "source": source,
        "header_hex": mapping.header_hex,
    }
    cache[key] = entry
    _save_cache(cache)
    logger.info("stored INIT_SYNC inner fp=%s via %s", mapping.class_id[:8], source)
    return INIT_SYNC_KEY


def ingest_init_response(session, raw: bytes, *, source: str = "init") -> dict[str, Any]:
    """Scan init body, map inners, store send classes + INIT_SYNC separately."""
    from pigeon_protocol.foundation.ws_blob_compute import _store_session_class_inner

    parsed = parse_init_response(raw)
    stored: list[str] = []
    for hit in scan_binary_for_inners(raw):
        inner = bytes.fromhex(hit["inner_hex"])
        mapping = map_inner_to_equiv_class(
            inner,
            offset=int(hit.get("offset") or 0),
            from_frame=bool(hit.get("from_frame")),
            context="init_top_level" if not hit.get("from_frame") else "ws_frame",
        )
        if mapping.send_usable:
            _store_session_class_inner(session, mapping.class_id, inner)
            stored.append(mapping.class_id)
        else:
            store_init_sync_inner(session, inner, source=source)
            stored.append(INIT_SYNC_KEY)

    return {
        "parsed": parsed.to_dict(),
        "stored_keys": stored,
        "send_from_init": [m.class_id for m in parsed.inners if m.send_usable],
        "init_sync": [m.class_id for m in parsed.inners if m.role == "INIT_SYNC"],
    }


def export_init_mapping(session, raw: bytes | None = None, path: Path | None = None) -> Path:
    """Write standalone_bundle/ws_inner_from_init.json."""
    out = path or (STANDALONE_BUNDLE / "ws_inner_from_init.json")
    doc = build_session_init_mapping(session, raw)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("exported init inner mapping -> %s", out)
    return out
