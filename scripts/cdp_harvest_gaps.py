#!/usr/bin/env python3
"""CDP harvest WS templates for gap lengths (priority + optional full ladder)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

GAP_PRIORITY = (
    64, 65, 67, 68, 70, 71, 73, 74,
    82, 83, 85, 86, 87, 88, 89,
    91, 92, 93, 94, 95, 97, 98, 99, 100,
    150, 200,
)


def main() -> int:
    from pigeon_protocol.capture_loader import index_send_templates
    from pigeon_protocol.ws_sign_bucket import coverage_report
    from pigeon_protocol.ws_template_harvest import (
        DEFAULT_LADDER,
        harvest_lengths,
        missing_lengths,
    )

    p = argparse.ArgumentParser()
    p.add_argument("--full", action="store_true", help="harvest full DEFAULT_LADDER")
    p.add_argument("--all-gaps", action="store_true", help="harvest all gaps 1-200 from coverage report")
    p.add_argument("--delay", type=float, default=0.6, help="seconds between sends")
    args = p.parse_args()

    if args.all_gaps:
        from pigeon_protocol.ws_sign_bucket import coverage_report
        from pigeon_protocol.ws_template_harvest import FEIGE_UI_MAX_TEXT_BYTES

        lengths = [
            n for n in (coverage_report().get("gaps_1_200") or [])
            if n <= FEIGE_UI_MAX_TEXT_BYTES
        ]
        ui_skipped = [
            n for n in (coverage_report().get("gaps_1_200") or [])
            if n > FEIGE_UI_MAX_TEXT_BYTES
        ]
    elif args.full:
        lengths = list(DEFAULT_LADDER)
    else:
        lengths = list(GAP_PRIORITY)
    missing = missing_lengths(lengths)
    harvested = asyncio.run(harvest_lengths(missing, delay_sec=args.delay)) if missing else 0
    report = {
        "missing_before": missing,
        "harvested": harvested,
        "pool_after": sorted(index_send_templates().keys()),
        "coverage_1_200": coverage_report().get("supported_count_1_200"),
        "still_missing": missing_lengths(lengths),
    }
    if args.all_gaps:
        report["ui_skipped_over_120"] = ui_skipped
        report["note"] = "121-200 harvested via maxlength bypass; >200 needs ComputedBlobStrategy"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
