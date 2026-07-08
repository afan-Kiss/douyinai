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

s = load_session()
html = _fetch_workspace_html(s)
report = {
    "html_len": len(html),
    "deskVersion_hits": re.findall(r'deskVersion["\']?\s*[:=]\s*["\']([^"\']+)', html)[:10],
    "vmok_hits": re.findall(r"@ecom-vmok/pigeon-im-pc:[^\"']+", html)[:10],
    "v_hits": sorted(set(re.findall(r"1\.0\.1\.\d{3,5}", html)), key=lambda x: int(x.rsplit(".", 1)[-1]))[-10:],
    "opt_version": re.findall(r"workstation_opt_version[^\"']{0,40}", html)[:5],
}
print(json.dumps(report, ensure_ascii=False, indent=2))
