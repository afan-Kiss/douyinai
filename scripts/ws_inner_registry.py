#!/usr/bin/env python3
"""Print 169B inner equivalence-class registry (ComputedBlobStrategy formula)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from pigeon_protocol.foundation.ws_blob_compute import (
        compute_inner_bytes,
        inner_class_for_text_b,
        registry_report,
    )
    from pigeon_protocol.session import load_session

    report = registry_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))

    session = load_session()
    probes = [6, 9, 30, 77, 82, 150]
    print("\n=== compute probe ===")
    for bl in probes:
        ic = inner_class_for_text_b(bl)
        if not ic:
            print(f"textB={bl}: no class")
            continue
        try:
            inner = compute_inner_bytes(session, bl)
            print(
                f"textB={bl:3d} class={ic.name} id={ic.class_id[:8]} "
                f"inner_head={inner[:8].hex()} ok"
            )
        except Exception as exc:
            print(f"textB={bl:3d} class={ic.name} FAIL {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
