#!/usr/bin/env python3
"""Deep 169B inner blob RE — field scan + session token correlation."""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.foundation.ws_blob_re import collect_inner_samples, compare_bucket_inners
from pigeon_protocol.session import load_session
from pigeon_protocol.ws_sign_decode import decode_blob


def _ascii_runs(inner: bytes, min_len: int = 4) -> list[str]:
    return [m.group(0).decode("ascii", errors="ignore") for m in re.finditer(rb"[ -~]{4,}", inner)]


def _token_hits(inner: bytes, session) -> dict[str, bool]:
    hay = inner + base64.b64encode(inner)
    checks = {
        "sessionid_prefix": (session.cookies.get("sessionid") or "")[:16],
        "verifyFp": session.query_tokens.get("verifyFp") or session.query_tokens.get("fp") or "",
        "pigeon_sign_prefix": (session.query_tokens.get("pigeon_sign") or "")[:24],
        "device_id": session.query_tokens.get("device_id") or "",
    }
    out: dict[str, bool] = {}
    for k, v in checks.items():
        if not v:
            out[k] = False
            continue
        out[k] = v.encode() in hay or v[:12].encode() in inner
    return out


def main() -> int:
    session = load_session()
    samples = collect_inner_samples()
    report: dict = {
        "session": {
            "cookies": len(session.cookies),
            "has_pigeon_sign": bool(session.query_tokens.get("pigeon_sign")),
            "ws_urls": len(session.ws_urls),
        },
        "samples": [],
        "bucket_diff": compare_bucket_inners(),
        "hypotheses": [],
    }

    for s in samples:
        inner = bytes.fromhex(s.inner_hex)
        report["samples"].append(
            {
                "textB": s.text_byte_length,
                "bucket": s.bucket,
                "source": s.source,
                "sha256_prefix": s.layout.get("sha256_prefix"),
                "ascii_runs": _ascii_runs(inner)[:8],
                "token_hits": _token_hits(inner, session),
                "head16_hex": inner[:16].hex(),
                "tail16_hex": inner[-16:].hex(),
            }
        )

    # Cross-sample: stable prefix bytes across bucket B
    b_inners = [bytes.fromhex(s.inner_hex) for s in samples if s.bucket == "B"]
    if len(b_inners) >= 2:
        stable = 0
        for i in range(min(len(x) for x in b_inners)):
            if all(x[i] == b_inners[0][i] for x in b_inners):
                stable += 1
            else:
                break
        report["bucket_B_stable_prefix_bytes"] = stable

    # MS4w ticket pattern (IM SDK)
    for s in samples:
        inner = bytes.fromhex(s.inner_hex)
        if b"MS4w" in inner or b"MS4w" in decode_blob(base64.b64encode(inner)[:100]):
            report["hypotheses"].append(f"MS4w ticket in bucket {s.bucket} sample {s.source}")

    out_path = ROOT / "analysis" / "ws_inner_re_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nwritten: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
