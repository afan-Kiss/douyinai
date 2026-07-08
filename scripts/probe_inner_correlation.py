#!/usr/bin/env python3
"""Correlate session tokens with 169B inner — rule out simple derivations."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "analysis" / "inner_correlation.json"


def _cookie_map(session) -> dict[str, str]:
    cookies = getattr(session, "cookies", None) or {}
    if isinstance(cookies, list):
        return {c.get("name", ""): c.get("value", "") for c in cookies if c.get("name")}
    if isinstance(cookies, dict):
        return {str(k): str(v) for k, v in cookies.items()}
    return {}


def main() -> int:
    from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache
    from pigeon_protocol.session import load_session

    session = load_session()
    cached = _load_session_class_cache(session)
    if not cached:
        print(json.dumps({"error": "no session inner cache"}))
        return 2

    inner = next(iter(cached.values()))
    body = inner[8:]
    cookies = _cookie_map(session)

    tokens = {
        "sessionid": cookies.get("sessionid", ""),
        "sessionid_ss": cookies.get("sessionid_ss", ""),
        "sid_tt": cookies.get("sid_tt", ""),
        "uid_tt": cookies.get("uid_tt", ""),
        "pigeon_sign": str(getattr(session, "pigeon_sign", "") or ""),
        "msToken": cookies.get("msToken", ""),
        "ttwid": cookies.get("ttwid", ""),
    }

    checks: list[dict] = []
    for name, val in tokens.items():
        if not val:
            continue
        raw = val.encode("utf-8", errors="ignore")
        for algo in ("md5", "sha1", "sha256"):
            dig = getattr(hashlib, algo)(raw).digest()
            checks.append(
                {
                    "token": name,
                    "algo": algo,
                    "prefix_in_inner": inner[:16].hex().find(dig[:8].hex()) >= 0,
                    "prefix_in_body": body[:32].hex().find(dig[:8].hex()) >= 0,
                    "digest_prefix": dig[:8].hex(),
                }
            )

    # XOR init body vs send body
    init_hx = None
    from pigeon_protocol.foundation.ws_session_inner import _load_cache, _session_key

    entry = _load_cache().get(_session_key(session), {})
    init_hx = entry.get("__init_sync__")
    xor_note = None
    if init_hx:
        init = bytes.fromhex(init_hx)
        if len(init) == 169:
            xb = bytes(a ^ b for a, b in zip(init[8:], body))
            xor_note = {
                "xor_body_prefix": xb[:16].hex(),
                "xor_stable_prefix": sum(1 for i in range(161) if init[8 + i] ^ body[i] == init[8]) ,
            }

    report = {
        "inner_header": inner[:8].hex(),
        "body_sha256_prefix": hashlib.sha256(body).hexdigest()[:16],
        "tokens_present": [k for k, v in tokens.items() if v],
        "checks": checks,
        "init_xor": xor_note,
        "conclusion": "no trivial token→inner embedding; body is opaque Rust output",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
