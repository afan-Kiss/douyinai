"""169B WS inner RE — layout dissection, corpus diff, Rust .node string scan."""
from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
NODE_PATH = ROOT / "analysis" / "feige_electron_sdk" / "rust-sdk-win32-x64-msvc" / "rust-sdk.win32-x64-msvc.node"
OUT_PATH = ROOT / "analysis" / "decompile_169b_report.json"

INNER_LEN = 169
HEADER_LEN = 8
BODY_LEN = 161

RUST_STRING_NEEDLES = (
    b"invoke_async",
    b"invokeAsync",
    b"create_message",
    b"CreateMessage",
    b"PigeonIMCreateMessage",
    b"packedMessage",
    b"packed_message",
    b"invalid access token",
    b"access token",
    b"accessToken",
    b"169",
    b"inner",
    b"encrypt",
    b"decrypt",
    b"aes",
    b"chacha",
    b"message_body",
    b"init_sdk",
    b"IMInit",
)


@dataclass
class InnerSample:
    label: str
    inner_hex: str
    source: str = ""
    text_b: int = 0
    bucket: str = ""


def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _protobuf_wire_scan(data: bytes, *, max_fields: int = 24) -> list[dict[str, Any]]:
    """Heuristic protobuf field scan (non-validating)."""
    fields: list[dict[str, Any]] = []
    i = 0
    while i < len(data) and len(fields) < max_fields:
        if i >= len(data):
            break
        tag = data[i]
        field_num = tag >> 3
        wire = tag & 0x07
        i += 1
        row: dict[str, Any] = {"offset": i - 1, "field": field_num, "wire": wire}
        try:
            if wire == 0:  # varint
                val = 0
                shift = 0
                while i < len(data):
                    b = data[i]
                    i += 1
                    val |= (b & 0x7F) << shift
                    if not (b & 0x80):
                        break
                    shift += 7
                row["varint"] = val
            elif wire == 2:  # length-delimited
                ln = data[i]
                i += 1
                if ln >= 128 and i < len(data):
                    ln = (ln & 0x7F) | (data[i] << 7)
                    i += 1
                row["len"] = ln
                row["bytes_hex"] = data[i : i + min(ln, 32)].hex()
                i += ln
            elif wire == 5:
                row["fixed32"] = data[i : i + 4].hex()
                i += 4
            elif wire == 1:
                row["fixed64"] = data[i : i + 8].hex()
                i += 8
            else:
                break
        except IndexError:
            break
        fields.append(row)
    return fields


def _scan_node_strings(path: Path, needles: tuple[bytes, ...], limit: int = 80) -> list[dict[str, Any]]:
    if not path.is_file():
        return [{"error": f"missing: {path}"}]
    data = path.read_bytes()
    hits: list[dict[str, Any]] = []
    for needle in needles:
        idx = 0
        while len(hits) < limit:
            idx = data.find(needle, idx)
            if idx == -1:
                break
            ctx = data[max(0, idx - 32) : idx + len(needle) + 64]
            hits.append(
                {
                    "needle": needle.decode("ascii", errors="replace"),
                    "offset": idx,
                    "offset_hex": hex(idx),
                    "context": ctx.decode("ascii", errors="replace")[:140],
                }
            )
            idx += len(needle)
    return hits


def _ascii_runs(data: bytes, min_len: int = 4) -> list[str]:
    return [m.group(0).decode("ascii", errors="ignore") for m in re.finditer(rb"[ -~]{4,}", data)]


def collect_samples() -> list[InnerSample]:
    samples: list[InnerSample] = []
    seen: set[str] = set()

    def add(label: str, hx: str, **kw: Any) -> None:
        if not hx or len(hx) != 338 or hx in seen:
            return
        seen.add(hx)
        samples.append(InnerSample(label=label, inner_hex=hx, **kw))

    try:
        from pigeon_protocol.foundation.ws_blob_re import collect_inner_samples

        for s in collect_inner_samples():
            add(f"capture_{s.bucket}_{s.text_byte_length}B", s.inner_hex, source=s.source, text_b=s.text_byte_length, bucket=s.bucket)
    except Exception:
        pass

    cache_path = __import__(
        "pigeon_protocol.account_context", fromlist=["inner_cache_file"]
    ).inner_cache_file()
    if cache_path.is_file():
        try:
            doc = json.loads(cache_path.read_text(encoding="utf-8"))
            for sk, entry in doc.items():
                if not isinstance(entry, dict):
                    continue
                for k, hx in entry.items():
                    if k.startswith("_"):
                        continue
                    add(f"cache_{sk[:8]}_{k[:8]}", str(hx), source="ws_inner_cache")
        except (OSError, json.JSONDecodeError):
            pass

    from pigeon_protocol.account_context import bundle_dir

    bundle = bundle_dir()
    for name in ("ws_inner_canonical.json", "ws_inner_from_init.json"):
        p = bundle / name
        if not p.is_file():
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            for row in doc.get("classes") or []:
                if isinstance(row, dict) and row.get("inner_hex"):
                    add(f"bundle_{row.get('name', '?')}", row["inner_hex"], source=name, bucket=str(row.get("name") or ""))
        except (OSError, json.JSONDecodeError):
            pass

    init_sync = bundle / "get_message_by_init_response.bin"
    if init_sync.is_file():
        try:
            from pigeon_protocol.foundation.ws_inner_bootstrap import scan_binary_for_inners

            for hit in scan_binary_for_inners(init_sync.read_bytes()):
                add(f"init_scan_{hit.get('class_id', '')[:8]}", hit["inner_hex"], source="init_response")
        except Exception:
            pass

    return samples


def dissect_inner(inner: bytes) -> dict[str, Any]:
    from pigeon_protocol.foundation.ws_inner_proto import MAGIC_EDBX, classify_inner

    variant_info = classify_inner(inner)
    if inner[:4] == MAGIC_EDBX:
        return {
            **variant_info,
            "full_sha256": hashlib.sha256(inner).hexdigest(),
            "payload_head32": inner[4:36].hex(),
            "ascii_runs": _ascii_runs(inner[4:])[:12],
        }

    hdr = inner[:8]
    body = inner[8:]
    le0, le4 = struct.unpack("<II", hdr)
    return {
        **variant_info,
        "header_hex": hdr.hex(),
        "header_le32": [le0, le4],
        "body_len": len(body),
        "body_entropy": round(_entropy(body), 3),
        "header_entropy": round(_entropy(hdr), 3),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "full_sha256": hashlib.sha256(inner).hexdigest(),
        "body_head32": body[:32].hex(),
        "body_tail32": body[-32:].hex(),
        "ascii_runs_body": _ascii_runs(body)[:12],
        "protobuf_wire_body": variant_info.get("protobuf_wire_body") or _protobuf_wire_scan(body),
        "magic_231a_at": body.find(bytes.fromhex("231a")) if body[:2] == bytes.fromhex("231a") else body.find(b"\x23\x1a"),
    }


def cross_login_diff(samples: list[InnerSample]) -> dict[str, Any]:
    bodies = {s.label: bytes.fromhex(s.inner_hex)[8:] for s in samples}
    if len(bodies) < 2:
        return {"note": "need >=2 distinct inners for diff"}

    labels = list(bodies.keys())
    ref_label, ref = labels[0], bodies[labels[0]]
    rows = []
    for lbl, body in bodies.items():
        if lbl == ref_label:
            continue
        same_prefix = 0
        for i in range(min(len(ref), len(body))):
            if ref[i] == body[i]:
                same_prefix += 1
            else:
                break
        xor_prefix = bytes(a ^ b for a, b in zip(ref[:16], body[:16])).hex()
        rows.append(
            {
                "a": ref_label,
                "b": lbl,
                "stable_prefix_bytes": same_prefix,
                "xor_head16": xor_prefix,
                "body_len_delta": len(body) - len(ref),
            }
        )
    return {"pairs": rows[:20]}


def decompile_report(*, scan_node: bool = True, probe_cipher: bool = True) -> dict[str, Any]:
    from pigeon_protocol.foundation.ws_inner_proto import InnerVariant, corpus_variant_summary

    samples = collect_samples()
    dissected = []
    inners: list[bytes] = []
    encrypted_bodies: list[bytes] = []
    for s in samples[:40]:
        inner = bytes.fromhex(s.inner_hex)
        inners.append(inner)
        info = dissect_inner(inner)
        if info.get("variant") == InnerVariant.ENCRYPTED_SEND.value:
            encrypted_bodies.append(inner[8:])
        row = {
            "label": s.label,
            "source": s.source,
            "text_b": s.text_b,
            "bucket": s.bucket,
            **info,
        }
        dissected.append(row)

    unified = len({s.inner_hex for s in samples}) == 1 and len(samples) > 1
    variant_summary = corpus_variant_summary(inners)
    report: dict[str, Any] = {
        "structure": {
            "total_bytes": INNER_LEN,
            "send_format": "8B class header (LE32×2) + 161B ring AES-GCM body (12 nonce + 133 ct + 16 tag)",
            "init_format": "4B edbX magic + 165B init ticket protobuf (NOT send-usable)",
            "generator": "Pigeon Rust SDK packedMessage cmd 11327 (Feige Electron)",
            "decode_path": "226B ASCII blob = std base64(169B) in WS frame signature region",
        },
        "sample_count": len(samples),
        "unified_session_mode": unified,
        "variant_summary": variant_summary,
        "samples": dissected,
        "cross_login_diff": cross_login_diff(samples),
        "known": [
            "Two 169B variants: encrypted_send (8+161 AES-GCM) vs edbX (4+165 ticket protobuf)",
            "edbX layout: prefix(8) + outer(149) + trailer(8); core {f1=0,f2=7,f3=route@110B}+0x20 suffix",
            "jinritemai live send uses edbX unified inner (65646258 header) — verified send_ok",
            "Pure Python: verify_sample_formula bit-exact vs Rust capture (formula_rebuild_sample: true)",
            "161B encrypted_send body fits ring AES-GCM 12+133+16; .node strings aes/gcm/access token",
            "Class header (8B) or edbX magic selects bucket; body identical within session (unified mode)",
        ],
        "unknown": [
            "edbX 8B trailer — session-scoped; cache from ingest or reverse accessToken UUID",
            "IM accessToken not in init HTTP — createUser 11200 via PIGEON_CREATE_USER_ONLY node hook",
            "Whether encrypted_send wraps same route protobuf before AES-GCM seal",
        ],
        "next_re": [
            "Reverse 8B trailer from accessToken (derive_trailer_candidates — no hit yet)",
            "Optional: PIGEON_CREATE_USER_ONLY=1 node feige_invoke for fresh UUID token",
            "Hook cmd 11327 — dump pre-seal plaintext for encrypted_send cross-check",
            "HKDF probe expanded (probe_inner_kdf.py) — 0 hits on current corpus; need fresh token",
        ],
    }

    if scan_node:
        try:
            from pigeon_protocol.foundation.ws_inner_node_re import node_re_report

            report["rust_node"] = node_re_report(NODE_PATH)
        except Exception as exc:
            report["rust_node"] = {
                "path": str(NODE_PATH),
                "size": NODE_PATH.stat().st_size if NODE_PATH.is_file() else 0,
                "string_hits": _scan_node_strings(NODE_PATH, RUST_STRING_NEEDLES),
                "error": str(exc),
            }

    if probe_cipher and encrypted_bodies:
        try:
            from pigeon_protocol.foundation.ws_inner_cipher_probe import probe_samples
            from pigeon_protocol.session import load_session

            session = load_session()
            report["cipher_probe"] = probe_samples(session, encrypted_bodies)
        except Exception as exc:
            report["cipher_probe"] = {"error": str(exc)}

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
