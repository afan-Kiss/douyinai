"""Analyze fn#150 and string pool for a_bogus constants."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.foundation.bdms_jsvmp import load_program
from pigeon_protocol.foundation.bdms_jsvmp_disasm import disasm_function


def main() -> None:
    prog, _ = load_program()
    pool = prog.string_pool
    for i, s in enumerate(pool):
        if any(
            k in s
            for k in (
                "Dkdpgh",
                "s3",
                "s4",
                "dhzx",
                "bds",
                "pageId",
                "RIGHT",
                "EMPTY",
            )
        ) or len(s) > 60 and "/" in s and s.isascii():
            print(i, repr(s[:120]))

    print("\n=== fn103 full ===")
    print(disasm_function(prog, 103)["disasm_text"])

    print("\n=== fn150 s3/s4 region ===")
    for ins in disasm_function(prog, 150)["insns"]:
        if 200 <= ins["pc"] <= 280 or ins["pc"] >= 1680:
            print(
                f"{ins['pc']:4d} {ins['op']:3d} {ins['name']:18s} "
                f"{ins['operands']} {ins['comment']}"
            )


if __name__ == "__main__":
    main()
