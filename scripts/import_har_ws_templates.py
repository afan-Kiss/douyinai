#!/usr/bin/env python3
"""Import signed WS send frames from HAR → live_ws_frame_sent_b{NNN}.json pool."""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.config import LIVE_CAPTURES
from pigeon_protocol.ws_sign_import import import_sample, sample_to_event


def _signed_send_events_from_dir(src: Path) -> list[dict]:
    from pigeon_protocol.ws_sign import locate_signature_region

    out: list[dict] = []
    for p in sorted(src.glob("har_*_ws_frame_sent_*.json")):
        try:
            ev = json.loads(p.read_text(encoding="utf-8"))
            payload = ev.get("payload") or ""
            if not payload:
                continue
            raw = base64.b64decode(payload)
            if not locate_signature_region(raw):
                continue
            ev["source"] = f"har_import:{p.name}"
            out.append(ev)
        except Exception:
            continue
    return out


def _signed_send_events_from_har(har_path: Path, out_dir: Path) -> list[dict]:
    sys.path.insert(0, str(ROOT / "scripts"))
    from parse_har import har_ws_messages_to_captures, load_har

    har = load_har(har_path)
    entries = (har.get("log") or {}).get("entries") or []
    out_dir.mkdir(parents=True, exist_ok=True)
    events: list[dict] = []
    for idx, entry in enumerate(entries):
        for ws_cap in har_ws_messages_to_captures(entry, idx):
            if ws_cap.get("type") != "ws_frame_sent":
                continue
            payload = ws_cap.get("payload") or ""
            if not payload:
                continue
            try:
                raw = base64.b64decode(payload)
            except Exception:
                continue
            from pigeon_protocol.ws_sign import locate_signature_region

            if not locate_signature_region(raw):
                continue
            name = f"har_{idx:05d}_{ws_cap['type']}_{ws_cap['request_id'].split('_')[-1]}.json"
            p = out_dir / name
            p.write_text(json.dumps(ws_cap, ensure_ascii=False, indent=2), encoding="utf-8")
            ws_cap["source"] = f"har:{har_path.name}:{name}"
            events.append(ws_cap)
    return events


def _stable_path(byte_len: int) -> Path:
    cap = LIVE_CAPTURES / "ws_sign"
    cap.mkdir(parents=True, exist_ok=True)
    return cap / f"live_ws_frame_sent_b{byte_len:03d}.json"


def import_events(events: list[dict], *, overwrite: bool = False) -> dict:
    from pigeon_protocol.capture_loader import index_send_templates
    from pigeon_protocol.ws_sign_bucket import coverage_report, is_supported_text_len

    saved: list[dict] = []
    skipped: list[dict] = []
    for ev in events:
        payload = ev.get("payload") or ""
        if not payload:
            continue
        try:
            raw = base64.b64decode(payload)
        except Exception:
            continue
        sample = {"b64": payload, "url": ev.get("url"), "len": len(raw), "source": ev.get("source")}
        event = sample_to_event(sample)
        bl = int(event.get("text_byte_length") or 0)
        if bl <= 0:
            skipped.append({"reason": "no_text_byte_length", "source": ev.get("source")})
            continue
        dest = _stable_path(bl)
        if dest.exists() and not overwrite:
            skipped.append({"textB": bl, "reason": "exists", "path": dest.name})
            continue
        dest.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
        saved.append({"textB": bl, "path": dest.name, "frame": event.get("payload_length")})

    pool = sorted(index_send_templates().keys())
    cov = coverage_report()
    return {
        "imported": saved,
        "skipped": skipped,
        "pool": pool,
        "supported_count_1_200": cov.get("supported_count_1_200"),
        "gaps_1_80": cov.get("summary", {}).get("unsupported_gaps_1_80", []),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Import signed WS frames from HAR into template pool")
    p.add_argument("--file", help="HAR file path")
    p.add_argument("--from-captures", action="store_true", help="scan captures/live/from_har only")
    p.add_argument("--overwrite", action="store_true")
    args = p.parse_args()

    events: list[dict] = []
    if args.from_captures or not args.file:
        events.extend(_signed_send_events_from_dir(LIVE_CAPTURES / "from_har"))
    if args.file:
        har = Path(args.file)
        if not har.exists():
            print(json.dumps({"ok": False, "error": f"not found: {har}"}))
            return 1
        events.extend(_signed_send_events_from_har(har, LIVE_CAPTURES / "from_har"))

    report = import_events(events, overwrite=args.overwrite)
    report["ok"] = bool(report["imported"])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] or report.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
