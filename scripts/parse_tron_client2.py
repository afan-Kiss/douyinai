#!/usr/bin/env python3
import re
from pathlib import Path

js = Path(__file__).resolve().parents[1].joinpath("download_vendor.js").read_text(encoding="utf-8", errors="replace")
for key in ("CHECK_API", "CURRENT_BUILD_API", "TronClientBase", "TronClient"):
    idx = 0
    while True:
        idx = js.find(key, idx)
        if idx == -1:
            break
        print("===", key)
        print(js[max(0, idx - 50) : idx + 500][:550])
        idx += len(key)
        if idx > 0 and js.count(key) > 15:
            break

js2 = Path(__file__).resolve().parents[1].joinpath("download_index.js").read_text(encoding="utf-8", errors="replace")
for key in ("TronClient", "pid", "checkForUpdate", "IM=", "new "):
    if key in js2:
        print("index has", key)
for m in re.finditer(r"pid[:\"'][^\"']{0,80}", js2):
    print("pid ctx", m.group()[:120])
for m in re.finditer(r"TronClient[^;]{0,200}", js2):
    print("tron", m.group()[:220])
