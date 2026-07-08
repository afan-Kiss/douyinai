#!/usr/bin/env python3
"""Extract jsvmp bytecode blob from bdms.js."""
from __future__ import annotations

import base64
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BDMS = ROOT / "analysis" / "bdms.js"
OUT_BIN = ROOT / "analysis" / "bdms_jsvmp.bin"
OUT_META = ROOT / "analysis" / "bdms_jsvmp_meta.txt"


def main() -> None:
    text = BDMS.read_text(encoding="utf-8")
    m = re.search(r'atob\("([A-Za-z0-9+/=]{100,})"\)', text)
    if not m:
        m = re.search(r'\("([A-Za-z0-9+/=]{200,})"\)', text)
    if not m:
        idx = text.find("UEsCA")
        if idx > 0:
            end = idx
            while end < len(text) and text[end] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=":
                end += 1
            b64 = text[idx:end]
            raw = base64.b64decode(b64 + "=" * ((4 - len(b64) % 4) % 4))
            OUT_BIN.write_bytes(raw)
            print(f"raw_len={len(raw)} magic={raw[:4]!r}")
            print(f"wrote {OUT_BIN}")
            return
    b64 = m.group(1)
    raw = base64.b64decode(b64)
    OUT_BIN.write_bytes(raw)
    meta = [
        f"b64_len={len(b64)}",
        f"raw_len={len(raw)}",
        f"magic={raw[:4]!r}",
        f"head_hex={raw[:64].hex()}",
    ]
    OUT_META.write_text("\n".join(meta), encoding="utf-8")
    print("\n".join(meta))
    print(f"wrote {OUT_BIN}")


if __name__ == "__main__":
    main()
