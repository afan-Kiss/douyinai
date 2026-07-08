#!/usr/bin/env python3
"""169B WS inner decompile / RE report — layout, corpus, Rust .node strings."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from pigeon_protocol.foundation.ws_inner_decompile import decompile_report

    report = decompile_report(scan_node=True, probe_cipher=True)
    node = report.get("rust_node") or {}
    exports = node.get("pe_exports") or []
    print(json.dumps(
        {
            "ok": True,
            "samples": report.get("sample_count"),
            "unified": report.get("unified_session_mode"),
            "variants": report.get("variant_summary"),
            "out": str(ROOT / "analysis" / "decompile_169b_report.json"),
            "node_re": str(ROOT / "analysis" / "node_re_169b.json"),
            "pe_exports": len(exports) if isinstance(exports, list) else 0,
            "cipher_hits": sum(len(p.get("hits") or []) for p in (report.get("cipher_probe") or [])),
            "next_re": report.get("next_re"),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if report.get("sample_count") else 1


if __name__ == "__main__":
    raise SystemExit(main())
