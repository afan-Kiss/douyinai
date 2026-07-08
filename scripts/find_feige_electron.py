#!/usr/bin/env python3
"""Search Windows paths for Feige / 飞鸽 Electron binaries (Pigeon Rust SDK)."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis" / "feige_electron_scan.json"

NEEDLES = (
    b"packedMessage",
    b"PigeonIMCreateMessage",
    b"invokeWithoutReturn",
    b"pigeon_sdk",
    b"webviewBridge",
    b"frontierSign",
)

SCAN_ROOTS = [
    Path("E:/feige-electron"),
    Path(os.environ.get("LOCALAPPDATA", "")),
    Path(os.environ.get("PROGRAMFILES", "")),
    Path(os.environ.get("PROGRAMFILES(X86)", "")),
    Path("D:/"),
    Path("E:/"),
]

DIR_HINTS = re.compile(r"feige|pigeon|jinritemai|doudian|抖店|飞鸽", re.I)
FILE_EXTS = {".exe", ".dll", ".node", ".asar", ".wasm", ".so"}


def scan_file(path: Path) -> dict | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 1024:
        return None
    hits = [n.decode("ascii", errors="ignore") for n in NEEDLES if n in data]
    if not hits:
        return None
    return {"path": str(path), "size": len(data), "hits": hits}


def iter_candidates(roots: list[Path], *, max_files: int = 8000) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dp = Path(dirpath)
                if len(found) >= max_files:
                    return found
                # prune deep/noisy trees
                dirnames[:] = [
                    d
                    for d in dirnames
                    if d not in {
                        "node_modules",
                        "Cache",
                        "GPUCache",
                        "Code Cache",
                        "Service Worker",
                        "Microsoft",
                        "Windows",
                        "Google",
                        "Mozilla",
                    }
                ]
                name_hit = DIR_HINTS.search(str(dp))
                for fn in filenames:
                    p = dp / fn
                    if p.suffix.lower() not in FILE_EXTS:
                        continue
                    if not name_hit and not DIR_HINTS.search(fn):
                        continue
                    key = str(p)
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(p)
                    if len(found) >= max_files:
                        return found
        except OSError:
            continue
    return found


def main() -> int:
    roots = [r for r in SCAN_ROOTS if r.is_dir()]
    candidates = iter_candidates(roots)
    matches: list[dict] = []
    for p in candidates:
        row = scan_file(p)
        if row:
            matches.append(row)
    report = {
        "roots": [str(r) for r in roots],
        "candidates_scanned": len(candidates),
        "matches": matches[:50],
        "ok": bool(matches),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
