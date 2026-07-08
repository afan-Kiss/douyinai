#!/usr/bin/env python3
"""Extract code snippets around interesting symbols in bdms.js."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "analysis" / "bdms.js").read_text(encoding="utf-8")
OUT = ROOT / "analysis" / "bdms_snippets.txt"

SYMS = ["URLSearchParams", "userAgent", "location", "localStorage", "cookie", "charCodeAt", "fromCharCode", "setRequestHeader"]


def snippet(text: str, pos: int, before: int = 200, after: int = 400) -> str:
    return text[max(0, pos - before) : pos + after]


def main() -> None:
    lines = []
    for sym in SYMS:
        lines.append(f"\n===== {sym} ({TEXT.count(sym)}) =====")
        for i, m in enumerate(re.finditer(re.escape(sym), TEXT)):
            if i >= 5:
                lines.append("... truncated ...")
                break
            lines.append(f"[{m.start()}] {snippet(TEXT, m.start())}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
