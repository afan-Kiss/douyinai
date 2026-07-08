#!/usr/bin/env python3
"""Scan Feige Roaming data for conversation_id / SDK init hints."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis" / "feige_local_scan.json"


def find_roaming_root() -> Path | None:
    base = Path.home() / "AppData/Roaming"
    for p in base.iterdir():
        if not p.is_dir():
            continue
        if (p / "rs_sdk").is_dir() or (p / "Partitions").is_dir():
            if "config.json" in [x.name for x in p.iterdir() if x.is_file()]:
                return p
    return None


def scan_bytes(data: bytes) -> dict:
    out: dict = {"conv_ids": [], "ws_tokens": [], "miha": []}
    for m in re.finditer(rb"0:1:\d{8,22}:\d{8,22}", data):
        s = m.group().decode("ascii", errors="ignore")
        if s not in out["conv_ids"]:
            out["conv_ids"].append(s)
    for m in re.finditer(rb"token=[A-Za-z0-9_+\-]{20,120}", data):
        t = m.group().decode("ascii", errors="ignore")[:80]
        if t not in out["ws_tokens"]:
            out["ws_tokens"].append(t)
    for m in re.finditer(rb"MIHA[A-Za-z0-9+/=_\-]{40,200}", data):
        s = m.group().decode("ascii", errors="ignore")[:60]
        if s not in out["miha"]:
            out["miha"].append(s)
    return out


def main() -> int:
    roaming = find_roaming_root()
    report: dict = {"roaming": str(roaming) if roaming else None, "files": [], "merged": {"conv_ids": [], "ws_tokens": [], "miha": []}}
    if not roaming:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    candidates: list[Path] = []
    for sub in (
        roaming / "Partitions" / "7649723789181673001" / "Local Storage" / "leveldb",
        roaming / "Partitions" / "7649723789181673001" / "Session Storage",
        roaming / "Session Storage",
        roaming / "rs_sdk",
    ):
        if sub.is_dir():
            candidates.extend(sub.rglob("*"))

    for p in candidates:
        if not p.is_file() or p.stat().st_size > 8_000_000:
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if b"0:1:" not in data and b"conversation" not in data.lower() and b"MIHA" not in data:
            continue
        row = {"path": str(p), "size": p.stat().st_size}
        row.update(scan_bytes(data))
        if row["conv_ids"] or row["ws_tokens"]:
            report["files"].append(row)
            for k in ("conv_ids", "ws_tokens", "miha"):
                for v in row[k]:
                    if v not in report["merged"][k]:
                        report["merged"][k].append(v)

    cfg = roaming / "config.json"
    if cfg.is_file():
        report["config"] = json.loads(cfg.read_text(encoding="utf-8"))

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["merged"]["conv_ids"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
