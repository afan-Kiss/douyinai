#!/usr/bin/env python3
"""Extract sdk-glue loadMap / security module hints from webpack bundle."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GLUE = ROOT / "analysis" / "sdk-glue.js"
OUT = ROOT / "analysis" / "glue_loadmap.json"


def main() -> int:
    text = GLUE.read_text(encoding="utf-8", errors="ignore")

    # loadMap keys referenced in glue
    load_keys = sorted(set(re.findall(r'loadMap\.([a-zA-Z0-9_]+)', text)))
    module_ids = sorted(set(re.findall(r'"([a-zA-Z][a-zA-Z0-9_/.\-]{3,60})":', text)))[:200]

    security_modules = [
        m
        for m in module_ids
        if any(x in m.lower() for x in ("mssdk", "acrawler", "frontier", "bdms", "security", "csrf"))
    ]

    # version strings like 1.0.1.20
    versions = sorted(set(re.findall(r'\d+\.\d+\.\d+\.\d+', text)))[:30]

    report = {
        "glue_bytes": len(text.encode("utf-8")),
        "load_map_keys": load_keys,
        "security_modules": security_modules,
        "version_strings": versions,
        "stable_base_urls": sorted(
            set(re.findall(r"https://lf[^\"']+rc-client-security[^\"']+", text))
        )[:20],
        "note": "glue orchestrates bdms/csrf; acrawler likely injected by Electron preload, not glue CDN",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
