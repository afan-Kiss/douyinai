"""WS 169B inner blob buckets — range rules + canonical templates."""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

# Empirical inner fingerprints (2026-07-06 live pool):
# A: 6B only | B: 9-60B identical inner | C: 77B | D: 78B


@dataclass(frozen=True)
class BucketSpec:
    name: str
    text_min: int
    text_max: int
    canonical_len: int


BUCKET_SPECS: tuple[BucketSpec, ...] = (
    BucketSpec("A", 6, 6, 6),
    BucketSpec("B", 9, 60, 9),
    BucketSpec("C", 77, 77, 77),
    BucketSpec("D", 78, 78, 78),
)

# Lengths outside official A/B/C/D specs need per-length harvest unless same 169B inner (empirical groups).
UNSUPPORTED_RANGES: tuple[tuple[int, int], ...] = ()

# End of contiguous long-text inner reuse (82–117 harvested; 118–119 same inner, UI cannot send).
INFERRED_INNER_EXTENSIONS: tuple[tuple[int, int], ...] = ((118, 119),)

# Live cross-bucket probes (2026-07-06): inner blob is NOT reusable across these gaps.
GAP_PROBE: tuple[dict[str, Any], ...] = (
    {"textB": 7, "cross_tpl": 6, "build_ok": False, "note": "7B ≠ bucket A (6B); needs harvest"},
    {"textB": 8, "cross_tpl": 6, "build_ok": False, "note": "8B ≠ bucket A (6B); needs harvest"},
    {"textB": 61, "cross_tpl": 60, "build_ok": False, "note": "61B ≠ bucket B inner at 60B"},
    {"textB": 79, "cross_tpl": 77, "build_ok": False, "note": "79B ≠ bucket C/D (77/78B)"},
)


def bucket_for_text_len(byte_len: int) -> BucketSpec | None:
    for spec in BUCKET_SPECS:
        if spec.text_min <= byte_len <= spec.text_max:
            return spec
    return None


@lru_cache(maxsize=1)
def _long_text_inner_fp() -> str | None:
    return _inner_fp_for(82) or _inner_fp_for(117)


def _in_long_text_group(byte_len: int) -> bool:
    fp = _long_text_inner_fp()
    if not fp:
        return False
    mapped = _inner_fp_for(byte_len)
    if mapped == fp:
        return True
    for lo, hi in INFERRED_INNER_EXTENSIONS:
        if lo <= byte_len <= hi:
            return True
    return False


def same_inner_bucket(a: int, b: int) -> bool:
    if a <= 0 or b <= 0:
        return False
    if a == b:
        return True
    sa, sb = bucket_for_text_len(a), bucket_for_text_len(b)
    if sa and sb:
        return sa.name == sb.name
    m = bucket_map()
    if m.get(a) and m.get(a) == m.get(b):
        return True
    if _in_long_text_group(a) and _in_long_text_group(b):
        return True
    return False


def is_supported_text_len(byte_len: int) -> bool:
    if byte_len <= 0:
        return False
    from pigeon_protocol.capture_loader import index_send_templates

    pool = index_send_templates()
    if byte_len in pool:
        return True
    spec = bucket_for_text_len(byte_len)
    if spec and spec.canonical_len in pool:
        return True
    return any(same_inner_bucket(bl, byte_len) for bl in pool)


def unsupported_reason(byte_len: int) -> str:
    if is_supported_text_len(byte_len):
        return ""
    from pigeon_protocol.capture_loader import index_send_templates

    if byte_len in index_send_templates():
        return ""
    spec = bucket_for_text_len(byte_len)
    if spec:
        return f"canonical template b{spec.canonical_len:03d} missing"
    if byte_len > 200:
        return f"textB {byte_len} > 200 — needs 226B ComputedBlobStrategy"
    for lo, hi in UNSUPPORTED_RANGES:
        if lo <= byte_len <= hi:
            return f"textB {byte_len} outside known inner buckets — harvest or RE required"
    return f"textB {byte_len} unsupported — no inner-group template"


def resolve_template_byte_len(byte_len: int) -> int:
    """Map any supported textB → canonical template length in pool."""
    from pigeon_protocol.capture_loader import index_send_templates

    pool = index_send_templates()
    if byte_len in pool:
        return byte_len

    spec = bucket_for_text_len(byte_len)
    if spec and spec.canonical_len in pool:
        return spec.canonical_len

    reuse = sorted(bl for bl in pool if same_inner_bucket(bl, byte_len))
    if reuse:
        return reuse[0]

    bmap = bucket_map()
    bid = bmap.get(byte_len)
    if bid:
        for bl, b in bmap.items():
            if b == bid and bl in pool:
                return bl
    canon = canonical_length_by_bucket()
    if bid and bid in canon and canon[bid] in pool:
        return canon[bid]
    return byte_len


def _inner_fingerprint(frame_b64: str) -> str | None:
    from pigeon_protocol.ws_sign import locate_signature_region
    from pigeon_protocol.ws_sign_decode import decode_blob

    try:
        data = base64.b64decode(frame_b64)
        region = locate_signature_region(data)
        if not region:
            return None
        blob = bytes(data[region.blob_start : region.blob_end])
        inner = decode_blob(blob)
        return hashlib.sha256(inner).hexdigest()
    except Exception:
        return None


@lru_cache(maxsize=1)
def _inner_groups() -> tuple[tuple[str, tuple[int, ...]], ...]:
    """Group harvested textB by identical 169B inner sha256."""
    bmap = bucket_map()
    grouped: dict[str, list[int]] = {}
    for bl, fp in bmap.items():
        grouped.setdefault(fp, []).append(bl)
    return tuple((fp, tuple(sorted(v))) for fp, v in sorted(grouped.items()))


def _inner_fp_for(byte_len: int) -> str | None:
    return bucket_map().get(byte_len)


@lru_cache(maxsize=1)
def bucket_map() -> dict[int, str]:
    """Map captured text byte-length → inner sha256 prefix."""
    from pigeon_protocol.capture_loader import index_send_templates, load_capture

    out: dict[int, str] = {}
    for bl, info in index_send_templates().items():
        try:
            ev = load_capture(info.path)
            fp = _inner_fingerprint(str(ev.get("payload") or ""))
            if fp:
                out[bl] = fp[:16]
        except Exception:
            continue
    return out


@lru_cache(maxsize=1)
def canonical_length_by_bucket() -> dict[str, int]:
    """Smallest captured byte-length per inner fingerprint."""
    bmap = bucket_map()
    canon: dict[str, int] = {}
    for bl, bid in sorted(bmap.items()):
        if bid not in canon or bl < canon[bid]:
            canon[bid] = bl
    # Merge with declared specs
    for spec in BUCKET_SPECS:
        canon.setdefault(spec.name, spec.canonical_len)
    return canon


def bucket_summary() -> dict[str, Any]:
    bmap = bucket_map()
    groups: dict[str, list[int]] = {}
    for bl, bid in sorted(bmap.items()):
        groups.setdefault(bid, []).append(bl)
    supported = [n for n in range(1, 81) if is_supported_text_len(n)]
    return {
        "buckets": groups,
        "canonical": canonical_length_by_bucket(),
        "specs": [s.__dict__ for s in BUCKET_SPECS],
        "supported_text_lengths_1_80": supported,
        "unsupported_gaps_1_80": [n for n in range(1, 81) if not is_supported_text_len(n)],
    }


def gap_harvest_plan(*, probe_build: bool = False) -> dict[str, Any]:
    """Prioritized CDP harvest targets to close textB gaps."""
    gaps = [n for n in range(1, 81) if not is_supported_text_len(n)]
    probe_targets = (7, 8, 61, 79, 62, 75, 76, 80)
    gap_probe: list[dict[str, Any]] = []

    engine = None
    if probe_build:
        from pigeon_protocol.foundation.ws_sign_engine import WsSendEngine
        from pigeon_protocol.ws_template_harvest import text_for_byte_length

        engine = WsSendEngine()

    for bl in probe_targets:
        spec = bucket_for_text_len(bl)
        cross = spec.canonical_len if spec else max(1, bl - 1)
        supported = is_supported_text_len(bl)
        build_ok = supported
        err = ""
        if probe_build and engine is not None:
            try:
                text = text_for_byte_length(bl) if bl >= 9 else ("好" * 3)[:bl] or "测"
                if len(text.encode("utf-8")) != bl:
                    text = "a" * bl
                payload = engine.build_frame(text)
                build_ok = len(payload) >= 3000
            except Exception as exc:
                build_ok = False
                err = str(exc)[:120]
        gap_probe.append(
            {
                "textB": bl,
                "cross_tpl": cross,
                "build_ok": build_ok,
                "supported": supported,
                "note": "" if build_ok else (err or GAP_PROBE_BY_LEN.get(bl, f"textB {bl} needs harvest")),
            }
        )

    priority = []
    for bl in (7, 8, 61, 62, 75, 76, 79, 80):
        if bl in gaps:
            priority.append(bl)
    priority.extend(bl for bl in gaps if bl not in priority)
    return {
        "gap_probe": gap_probe,
        "unsupported_ranges": list(UNSUPPORTED_RANGES),
        "gaps_1_80": gaps,
        "harvest_priority": priority[:12],
        "harvest_cmd": "python scripts/cdp_ws_sign_stack.py  # CDP :9222 + Feige tab",
    }


GAP_PROBE_BY_LEN: dict[int, str] = {
    7: "7B ≠ bucket A (6B); needs harvest",
    8: "8B ≠ bucket A (6B); needs harvest",
    61: "61B ≠ bucket B inner at 60B",
    79: "79B ≠ bucket C/D (77/78B)",
}


def coverage_report() -> dict[str, Any]:
    """Official-parity send coverage for UTF-8 text lengths."""
    from pigeon_protocol.capture_loader import index_send_templates

    pool = sorted(index_send_templates().keys())
    return {
        "canonical_templates": pool,
        "official_equivalent_ranges": [
            {"bucket": s.name, "textB": f"{s.text_min}-{s.text_max}", "canonical": s.canonical_len}
            for s in BUCKET_SPECS
        ],
        "supported_count_1_200": sum(1 for n in range(1, 201) if is_supported_text_len(n)),
        "total_1_200": 200,
        "gaps_1_200": [n for n in range(1, 201) if not is_supported_text_len(n)],
        "gap_harvest": gap_harvest_plan(),
        "summary": bucket_summary(),
    }
