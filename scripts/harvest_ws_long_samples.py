#!/usr/bin/env python3
"""Harvest WS send templates for textB > 200 via CDP + Feige UI."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

REPORT_PATH = ROOT / "analysis" / "ws_long_samples.json"


def _inner_fp_for_length(byte_len: int) -> str | None:
    from pigeon_protocol.capture_loader import index_send_templates, load_capture
    from pigeon_protocol.ws_sign_bucket import _inner_fingerprint

    info = index_send_templates().get(byte_len)
    if not info:
        return None
    try:
        ev = load_capture(info.path)
        return _inner_fingerprint(str(ev.get("payload") or ""))
    except Exception:
        return None


def build_report(harvest_report: dict) -> dict:
    from pigeon_protocol.capture_loader import index_send_templates
    from pigeon_protocol.ws_sign_bucket import bucket_map, is_supported_text_len, same_inner_bucket
    from pigeon_protocol.ws_template_harvest import LONG_MESSAGE_LADDER, text_for_byte_length

    pool = index_send_templates()
    long_pool = sorted(bl for bl in pool if bl > 200)
    rows = []
    for bl in harvest_report.get("requested") or LONG_MESSAGE_LADDER:
        fp = _inner_fp_for_length(bl)
        rows.append(
            {
                "textB": bl,
                "in_pool": bl in pool,
                "supported": is_supported_text_len(bl),
                "send_text_preview": text_for_byte_length(bl)[:48],
                "inner_fp": (fp or "")[:16],
                "same_inner_as_200": same_inner_bucket(bl, 200) if bl != 200 else True,
            }
        )
    return {
        "harvest": harvest_report,
        "long_pool_lengths": long_pool,
        "samples": rows,
        "inner_groups_over_200": {
            str(bl): bucket_map().get(bl) for bl in long_pool
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Harvest WS templates for textB > 200")
    parser.add_argument("--port", type=int, default=9222, help="Chrome CDP port")
    parser.add_argument(
        "--lengths",
        default="",
        help="comma-separated byte lengths (default: LONG_MESSAGE_LADDER)",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="per-send capture timeout (sec)")
    parser.add_argument("--delay", type=float, default=2.0, help="delay between sends (sec)")
    parser.add_argument("--dry-run", action="store_true", help="only print plan, no CDP sends")
    args = parser.parse_args()

    from pigeon_protocol.ws_template_harvest import (
        LONG_MESSAGE_LADDER,
        bootstrap_long_templates_sync,
        missing_lengths,
    )

    lengths: list[int] | None = None
    if args.lengths.strip():
        lengths = [int(x.strip()) for x in args.lengths.split(",") if x.strip()]

    want = list(lengths or LONG_MESSAGE_LADDER)
    missing = missing_lengths(want)
    print(json.dumps({"requested": want, "missing_before": missing}, ensure_ascii=False, indent=2))

    if args.dry_run:
        return 0

    if not missing:
        print("All requested lengths already in pool.")
        harvest_report = {"requested": want, "missing_before": [], "harvested": 0, "still_missing": []}
    else:
        harvest_report = bootstrap_long_templates_sync(
            lengths=want,
            port=args.port,
            timeout_sec=args.timeout,
            delay_sec=args.delay,
        )
        print(json.dumps(harvest_report, ensure_ascii=False, indent=2))

    report = build_report(harvest_report)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report -> {REPORT_PATH}")
    return 0 if not report["harvest"].get("still_missing") else 1


if __name__ == "__main__":
    try:
        from pigeon_protocol.runtime_paths import apply_runtime_env

        apply_runtime_env()
    except Exception:
        pass
    raise SystemExit(main())
