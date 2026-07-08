#!/usr/bin/env python3
"""Send helper — pick template length or suggest bootstrap."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    from pigeon_protocol.capture_loader import index_send_templates
    from pigeon_protocol.ws_template_harvest import text_for_byte_length

    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "好的"
    byte_len = len(text.encode("utf-8"))
    pool = sorted(index_send_templates().keys())
    if byte_len in pool:
        print(f"OK text={text!r} byte_len={byte_len} template=b{byte_len:03d}")
        return
    # nearest pool length
    nearest = min(pool, key=lambda n: abs(n - byte_len)) if pool else byte_len
    filler = text_for_byte_length(nearest)
    print(f"MISSING byte_len={byte_len} available={pool}")
    print(f"  use exact-length filler: {filler!r} ({nearest}B)")
    print(f"  or run: python run.py bootstrap --allow-partial")


if __name__ == "__main__":
    main()
