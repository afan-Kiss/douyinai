"""WS 226B blob RE helpers — inner layout analysis (foundation for computed sign)."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from pigeon_protocol.ws_sign import locate_signature_region
from pigeon_protocol.ws_sign_decode import analyze_frame, decode_blob, guess_inner_layout


@dataclass
class InnerSample:
    text_byte_length: int
    bucket: str
    inner_hex: str
    layout: dict[str, Any]
    frame_length: int
    source: str = ""


def collect_inner_samples() -> list[InnerSample]:
    from pigeon_protocol.capture_loader import index_send_templates, load_capture
    from pigeon_protocol.ws_sign_bucket import bucket_for_text_len

    out: list[InnerSample] = []
    for bl, info in sorted(index_send_templates().items()):
        try:
            ev = load_capture(info.path)
            raw = base64.b64decode(str(ev.get("payload") or ""))
            region = locate_signature_region(raw)
            if not region:
                continue
            inner = decode_blob(region.blob)
            spec = bucket_for_text_len(bl)
            out.append(
                InnerSample(
                    text_byte_length=bl,
                    bucket=spec.name if spec else "?",
                    inner_hex=inner.hex(),
                    layout=guess_inner_layout(inner),
                    frame_length=len(raw),
                    source=info.path.name,
                )
            )
        except Exception:
            continue
    return out


def compare_bucket_inners() -> dict[str, Any]:
    """Diff 169B payloads across canonical buckets A/B/C/D."""
    samples = collect_inner_samples()
    by_bucket: dict[str, InnerSample] = {}
    for s in samples:
        if s.bucket not in by_bucket or s.text_byte_length < by_bucket[s.bucket].text_byte_length:
            by_bucket[s.bucket] = s

    report: dict[str, Any] = {"canonical_by_bucket": {k: v.source for k, v in by_bucket.items()}}
    keys = sorted(by_bucket.keys())
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            sa, sb = by_bucket[a], by_bucket[b]
            ia, ib = bytes.fromhex(sa.inner_hex), bytes.fromhex(sb.inner_hex)
            diff = [j for j in range(min(len(ia), len(ib))) if ia[j] != ib[j]]
            report[f"{a}_vs_{b}"] = {
                "diff_bytes": len(diff),
                "first_diffs": diff[:24],
                "layout_a": sa.layout,
                "layout_b": sb.layout,
            }
    return report


def re_status() -> dict[str, Any]:
    """RE progress snapshot for foundation status."""
    samples = collect_inner_samples()
    cmp = compare_bucket_inners()
    fingerprints = {
        s.bucket: {
            "textB": s.text_byte_length,
            "sha256_prefix": s.layout.get("sha256_prefix"),
            "le32_0": s.layout.get("le32_0"),
            "source": s.source,
        }
        for s in samples
        if s.bucket in ("A", "B", "C", "D")
    }
    from pigeon_protocol.foundation.ws_blob_compute import inner_class_registry, registry_report
    from pigeon_protocol.ws_inner_buckets import BUCKET_INNER_FP
    from pigeon_protocol.ws_sign_bucket import gap_harvest_plan

    reg = inner_class_registry()
    return {
        "sample_count": len(samples),
        "buckets_observed": sorted({s.bucket for s in samples}),
        "equivalence_classes": len(reg),
        "class_registry": registry_report(),
        "bucket_inner_fingerprints": BUCKET_INNER_FP,
        "empirical_inner_groups": {
            "E": "ec682565 short/transitional (1-8,61,79-80)",
            "F": "a8acd6bd long-text (82-119 cross-length)",
            "G": "09edf723 120+ cluster",
        },
        "bucket_fingerprints": fingerprints,
        "computed_sign_ready": len(reg) >= 4,
        "computed_formula": "inner(textB) = session_constant[class(textB)]",
        "session_inner_cache": True,
        "inner_bucket_classifier": True,
        "bucket_cross_length_ready": True,
        "gap_harvest": gap_harvest_plan(),
        "notes": [
            "ComputedBlobStrategy: 7 equivalence classes, class(textB) selector formula",
            "169B body is session-scoped IM SDK output — cached after first send per class",
            "121-200: class F/G subgroups; >200 needs further RE",
            "WS URL cold start: workspace HTML + get_message_by_init + token synthesis",
        ],
        "re_targets": [
            "Pigeon Rust packedMessage offline crypto (cmd 11327 PigeonIMCreateMessage)",
        ],
        "bucket_diff": cmp,
    }
