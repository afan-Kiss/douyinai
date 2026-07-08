#!/usr/bin/env python3
"""Parse bdms jsvmp inflated VM structure — pure protocol, no browser."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "analysis" / "bdms_jsvmp_structure.json"


def main() -> int:
    from pigeon_protocol.foundation.bdms_jsvmp import deep_report, load_program

    prog, meta = load_program()
    report = {"meta": meta, **deep_report(prog)}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nwritten: {OUT}")
    return 0 if prog.bytes_consumed == prog.bytes_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
