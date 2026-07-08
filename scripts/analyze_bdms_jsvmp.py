#!/usr/bin/env python3
"""bdms jsvmp bytecode — extract + XOR decrypt (loader from bdms.js W/init)."""
from __future__ import annotations

import base64
import re
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "analysis" / "bdms_jsvmp.bin"
BDMS = ROOT / "analysis" / "bdms.js"
OUT_DEC = ROOT / "analysis" / "bdms_jsvmp_dec.bin"
OUT_INF = ROOT / "analysis" / "bdms_jsvmp_inflated.bin"
OUT_META = ROOT / "analysis" / "bdms_jsvmp_decrypt.json"


def _xor_byte(char_code: int, key: int, index: int) -> int:
    """bdms.js: function _(t,e){ return (t.charCodeAt(0)^(this+this%10*e)%256)>>>0 }"""
    return (char_code ^ ((key + (key % 10) * index) % 256)) & 0xFF


def decrypt_jsvmp(raw: bytes) -> tuple[bytes, dict]:
    """Decrypt VM payload: sum bytes[4:8] → key, XOR slice[8:] with rolling index."""
    if len(raw) < 8:
        raise ValueError("payload too short")
    key = sum(raw[i] for i in range(4, 8)) % 256
    body = raw[8:]
    out = bytearray(len(body))
    for i, b in enumerate(body):
        out[i] = _xor_byte(b, key, i)
    meta = {
        "raw_len": len(raw),
        "key_sum_bytes_4_7": sum(raw[i] for i in range(4, 8)),
        "key": key,
        "decrypted_len": len(out),
        "decrypted_magic": out[:4].hex(),
        "decrypted_head32": out[:32].hex(),
    }
    return bytes(out), meta


def inflate_jsvmp(decrypted: bytes) -> tuple[bytes, dict]:
    """bdms uses raw deflate (wbits=-15) after XOR — see T/k() in bdms.js."""
    meta: dict = {}
    try:
        inflated = zlib.decompress(decrypted, -zlib.MAX_WBITS)
        meta["inflate_ok"] = True
        meta["inflated_len"] = len(inflated)
        meta["inflated_head32"] = inflated[:32].hex()
        # ASCII probe for VM strings
        runs = re.findall(rb"[ -~]{6,}", inflated[:4096])
        meta["ascii_runs_head"] = [r[:64].decode("ascii", errors="ignore") for r in runs[:12]]
        return inflated, meta
    except Exception as exc:
        meta["inflate_ok"] = False
        meta["inflate_error"] = str(exc)
        return b"", meta


def extract_b64_from_bdms() -> bytes:
    text = BDMS.read_text(encoding="utf-8")
    m = re.search(r'W\([^)]*\)\("([A-Za-z0-9+/=]{1000,})"', text)
    if not m:
        idx = text.find("UEsCA")
        end = idx
        while end < len(text) and text[end] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=":
            end += 1
        b64 = text[idx:end]
    else:
        b64 = m.group(1)
    pad = (4 - len(b64) % 4) % 4
    return base64.b64decode(b64 + "=" * pad)


def scan_decrypted(dec: bytes) -> dict:
    info: dict = {}
    if dec[:2] == b"PK":
        info["looks_like_zip"] = True
        for i in range(min(len(dec) - 4, 65536)):
            if dec[i : i + 2] == b"PK":
                info.setdefault("pk_offsets", []).append(i)
                if len(info["pk_offsets"]) >= 8:
                    break
    for sig, name in [(b"\x78\x9c", "zlib"), (b"\x78\x01", "zlib_low"), (b"\x78\xda", "zlib_best")]:
        if dec.startswith(sig):
            info["compression"] = name
            try:
                info["inflate_len"] = len(zlib.decompress(dec))
            except Exception as exc:
                info["inflate_error"] = str(exc)
    info["entropy_unique_first4k"] = len(set(dec[:4096]))
    return info


def main() -> int:
    raw = BIN.read_bytes() if BIN.exists() else extract_b64_from_bdms()
    if not BIN.exists():
        BIN.write_bytes(raw)
        print(f"wrote {BIN} ({len(raw)} bytes)")

    dec, meta = decrypt_jsvmp(raw)
    OUT_DEC.write_bytes(dec)
    inflated, infl_meta = inflate_jsvmp(dec)
    if inflated:
        OUT_INF.write_bytes(inflated)
    scan = scan_decrypted(dec)
    struct_summary: dict = {}
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from pigeon_protocol.foundation.bdms_jsvmp import deep_report, parse_inflated

        prog = parse_inflated(inflated)
        struct_summary = {
            "string_pool": len(prog.string_pool),
            "functions": len(prog.functions),
            "bytes_consumed": prog.bytes_consumed,
            "sign_xrefs_count": len(deep_report(prog).get("sign_xrefs", [])),
        }
    except Exception as exc:
        struct_summary = {"parse_error": str(exc)}

    report = {"encrypt": meta, "inflate": infl_meta, "scan": scan, "structure": struct_summary}

    import json

    OUT_META.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nwritten: {OUT_DEC} ({len(dec)} bytes)")
    if inflated:
        print(f"inflated: {OUT_INF} ({len(inflated)} bytes)")
    print(f"meta: {OUT_META}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
