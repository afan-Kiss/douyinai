#!/usr/bin/env python3
"""Disassemble bdms jsvmp sign-related VM functions (pure, no browser)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "analysis" / "bdms_jsvmp_disasm.json"
SIGN_FNS = [85, 105, 107, 110, 111, 113, 115, 126, 156, 202]


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--fn", type=int, action="append", default=[])
    p.add_argument("--list-sign", action="store_true")
    args = p.parse_args()

    from pigeon_protocol.foundation.bdms_jsvmp import load_program
    from pigeon_protocol.foundation.bdms_jsvmp_disasm import (
        disasm_function,
        entry_points,
        sign_flow_summary,
    )

    prog, meta = load_program()
    targets = args.fn or SIGN_FNS

    report: dict = {
        "meta": meta,
        "entry_points_W": entry_points(prog),
        "functions": {},
    }

    for fi in targets:
        if fi >= len(prog.functions):
            continue
        report["functions"][str(fi)] = {
            **disasm_function(prog, fi),
            "sign_flow": sign_flow_summary(prog, fi),
        }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.list_sign:
        for fi in targets:
            sf = sign_flow_summary(prog, fi)
            print(f"=== fn #{fi} argc={sf['arg_count']} callees={sf['callees']} ===")
            for s in sf["strings"]:
                if any(k in s["s"].lower() for k in ("bogus", "token", "url", "fetch", "sign", "fp")):
                    print(f"  @{s['pc']:3d} {s['s']}")
            print(sf.get("disasm_around_abogus") or sf["disasm_head"][:800])
            print()
    else:
        print(json.dumps({k: v.get("sign_flow") for k, v in report["functions"].items()}, ensure_ascii=False, indent=2))

    print(f"\nwritten: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
