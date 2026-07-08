#!/usr/bin/env python3
"""Static analysis helpers for bdms.js."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BDMS = ROOT / "analysis" / "bdms.js"
OUT = ROOT / "analysis" / "bdms_static.json"


def main() -> None:
    text = BDMS.read_text(encoding="utf-8")
    report: dict = {"length": len(text), "version": None, "exports": [], "hooks": [], "interesting": {}}

    m = re.search(r"/\* V ([\d.]+) \*/", text)
    if m:
        report["version"] = m.group(1)

    # exported API block
    exp = re.search(r"r\.d\(n,\{([^}]+)\}\)", text)
    if exp:
        report["exports"] = re.findall(r"(\w+):function", exp.group(1))

    keys = [
        "XMLHttpRequest", "fetch", "open", "send", "prototype",
        "URLSearchParams", "location", "navigator", "userAgent",
        "msToken", "verifyFp", "charCodeAt", "fromCharCode",
        "WebSocket", "localStorage", "document", "cookie",
    ]
    for k in keys:
        report["interesting"][k] = text.count(k)

    # find xhr open hook pattern
    for pat in [
        r"XMLHttpRequest[^;]{0,500}",
        r"\.open\s*=\s*function[^;]{0,400}",
        r"fetch\s*=\s*function[^;]{0,400}",
        r"prototype\.open[^;]{0,400}",
    ]:
        m = re.search(pat, text)
        if m:
            report["hooks"].append({"pattern": pat[:40], "snippet": m.group(0)[:500]})

    # string array / decoder hints (common obfuscation)
    report["hex_strings"] = len(re.findall(r"\\x[0-9a-fA-F]{2}", text))
    report["unicode_esc"] = len(re.findall(r"\\u[0-9a-fA-F]{4}", text))

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
