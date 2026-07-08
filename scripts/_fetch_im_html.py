#!/usr/bin/env python3
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.feige_init import _fetch_workspace_html
from pigeon_protocol.session import load_session

html = _fetch_workspace_html(load_session())
print("html_len", len(html))
srcs = re.findall(r'src=["\']([^"\']+)["\']', html)
for u in srcs:
    if any(k in u.lower() for k in ("pigeon", "im", "message", "chat", "feige", "jinritemai")):
        print(u[:200])
print("total_scripts", len(srcs))
for pat in ("wasm", "sign", "WebSocket", "client_message", "pigeon_sign"):
    print(pat, len(re.findall(pat, html, re.I)))
