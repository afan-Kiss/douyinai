#!/usr/bin/env python3
"""Print 169B inner layout + synthesis status."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from pigeon_protocol.foundation.ws_inner_layout import layout_report
    from pigeon_protocol.foundation.ws_inner_synthesize import synthesis_status
    from pigeon_protocol.session import load_session

    session = load_session()
    report = {
        "layout": layout_report(),
        "synthesis": synthesis_status(session),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
