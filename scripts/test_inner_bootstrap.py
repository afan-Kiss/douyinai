#!/usr/bin/env python3
"""Test inner bootstrap + cold cache clear simulation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from pigeon_protocol.foundation.ws_blob_compute import compute_inner_bytes, inner_class_for_text_b
    from pigeon_protocol.foundation.ws_inner_bootstrap import ensure_session_inners, load_bundle_canonical
    from pigeon_protocol.session import load_session

    session = load_session()
    bundle = load_bundle_canonical(session)
    print("bundle classes:", len(bundle))

    report = ensure_session_inners(session, min_classes=4)
    print("ensure:", json.dumps(report, ensure_ascii=False))

    for bl in (6, 9, 45, 77, 150):
        ic = inner_class_for_text_b(bl)
        inner = compute_inner_bytes(session, bl, bootstrap=True)
        print(f"textB={bl:3d} class={ic.name if ic else '?'} head={inner[:8].hex()}")
    return 0 if report.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
