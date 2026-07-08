#!/usr/bin/env python3
"""Deep scan IM SDK chunk 424 for 169/226/wasm sign patterns."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHUNK = ROOT / "analysis" / "im_sdk" / "424.a645c9ba7e.js"
OUT = ROOT / "analysis" / "im_sdk_424_sign_re.json"


def near_pairs(text: str) -> list[dict]:
    """Find 169 and 226 within 200 chars — likely blob length constants."""
    out: list[dict] = []
    for m in re.finditer(r"\b169\b", text):
        chunk = text[m.start() : m.start() + 250]
        if "226" in chunk or "base64" in chunk.lower() or "sign" in chunk.lower():
            out.append({"pos": m.start(), "ctx": chunk.replace("\n", " ")[:240]})
    return out[:30]


def wasm_blobs(text: str) -> list[dict]:
    """Find embedded wasm magic \\0asm in base64 or binary."""
    out: list[dict] = []
    for m in re.finditer(r"AGFzb[A-Za-z0-9+/=]{200,}", text):
        out.append({"kind": "b64_wasm", "pos": m.start(), "len": len(m.group(0)), "head": m.group(0)[:80]})
    if b"\x00asm" in text.encode("latin-1", errors="ignore"):
        out.append({"kind": "raw_wasm_magic", "pos": text.find("\x00asm")})
    return out[:10]


def sign_funcs(text: str) -> list[dict]:
    patterns = [
        r"function\s+(\w*sign\w*)\s*\(",
        r"(\w*sign\w*)\s*:\s*function",
        r"\"(\w*sign\w*)\"\s*,\s*function",
        r"(\w*Sign\w*)\(",
        r"encrypt\w*Message",
        r"build\w*Blob",
        r"gen\w*Sign",
    ]
    hits: list[dict] = []
    for pat in patterns:
        for m in re.finditer(pat, text):
            name = m.group(1) if m.lastindex else m.group(0)
            if len(name) > 60:
                continue
            hits.append({"pattern": pat, "name": name, "pos": m.start()})
    # dedupe by name
    seen: set[str] = set()
    uniq: list[dict] = []
    for h in sorted(hits, key=lambda x: x["pos"]):
        if h["name"] in seen:
            continue
        seen.add(h["name"])
        uniq.append(h)
    return uniq[:80]


def main() -> int:
    if not CHUNK.is_file():
        print("missing", CHUNK, file=sys.stderr)
        return 1
    text = CHUNK.read_text(encoding="utf-8", errors="ignore")
    report = {
        "file": CHUNK.name,
        "bytes": CHUNK.stat().st_size,
        "near_169_226": near_pairs(text),
        "wasm_blobs": wasm_blobs(text),
        "sign_funcs": sign_funcs(text),
        "keyword_counts": {
            k: len(re.findall(re.escape(k), text, re.I))
            for k in ("169", "226", "wasmBinary", "instantiate", "WebAssembly", "client_message_id", "pigeon")
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(
        {
            "near_pairs": len(report["near_169_226"]),
            "wasm_blobs": len(report["wasm_blobs"]),
            "sign_funcs": len(report["sign_funcs"]),
            "out": str(OUT),
        },
        indent=2,
    ))
    for row in report["near_169_226"][:5]:
        print("---", row["ctx"][:200])
    for row in report["sign_funcs"][:15]:
        print("fn", row["name"], "@", row["pos"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
