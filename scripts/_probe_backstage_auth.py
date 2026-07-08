#!/usr/bin/env python3
"""Probe pigeon backstage auth — workspace HTML hints + API smoke."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.feige_init import probe_backstage_session, _fetch_workspace_html
from pigeon_protocol.session import load_session


def main() -> int:
    s = load_session()
    html = _fetch_workspace_html(s)
    print("workspace_html_len", len(html))
    for pat in ("get_link_info", "getLinkInfo", "passport", "biz_token", "login/callback"):
        m = re.search(pat, html, re.I)
        print(f"  {pat}:", "yes" if m else "no")
    for pat in (r"msgServiceId\D+(\d+)", r"temaiServiceId\D+(\d+)"):
        hits = re.findall(pat, html)
        if hits:
            print(f"  {pat}", hits[:3])

    probe = probe_backstage_session(s)
    print("backstage_probe", probe)
    return 0 if probe.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
