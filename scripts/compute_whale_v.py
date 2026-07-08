#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.feige_init import _fetch_workspace_html
from pigeon_protocol.session import load_session


def compute_whale_v(gfdata_ver: str, *, is_desk: bool = False) -> str:
    """Mirror IM SDK E.AM() — gfdatav1.ver last segment + offset."""
    parts = (gfdata_ver or "1.0.0.0").split(".")
    last = parts.pop() if parts else "0"
    try:
        bump = 695 if is_desk else 1401
        parts.append(str(int(last or "0") + bump))
    except ValueError:
        parts.append(last)
    return ".".join(parts)


def extract_gfdata_ver(html: str) -> str:
    for pat in (
        r'gfdatav1\s*=\s*\{[^}]*"ver"\s*:\s*"([^"]+)"',
        r'"ver"\s*:\s*"(\d+\.\d+\.\d+\.\d+)"',
        r'window\.gfdatav1[^;]{0,200}',
    ):
        m = re.search(pat, html)
        if m:
            if m.lastindex:
                return m.group(1)
            # try ver inside match
            vm = re.search(r'"ver"\s*:\s*"([^"]+)"', m.group(0))
            if vm:
                return vm.group(1)
    return ""


s = load_session()
html = _fetch_workspace_html(s)
gf = extract_gfdata_ver(html)
vmok = re.findall(r"@ecom-vmok/pigeon-im-pc:([^\"']+)", html)
report = {
    "gfdata_ver": gf,
    "whale_v_web": compute_whale_v(gf, is_desk=False) if gf else "",
    "whale_v_desk": compute_whale_v(gf, is_desk=True) if gf else "",
    "vmok": vmok[:5],
    "gfdata_snippet": html[html.find("gfdatav1") : html.find("gfdatav1") + 300] if "gfdatav1" in html else "",
}
print(json.dumps(report, ensure_ascii=False, indent=2))
