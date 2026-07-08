#!/usr/bin/env python3
import re
from pathlib import Path

js = Path(__file__).resolve().parents[1].joinpath("download_vendor.js").read_text(encoding="utf-8", errors="replace")
for key in ("tron-client", "tronClient", "memorize", "branch", "updateServer", "updateUrl", "cdn", "byted", "byte", "ecom", "feige", "doudian", "latest", "yml", "yaml"):
    if key.lower() in js.lower():
        print("has", key)

# extract quoted strings containing update/download/version
strings = re.findall(r'"([^"]{8,300})"', js)
interesting = []
for s in strings:
    sl = s.lower()
    if any(k in sl for k in ("http", "update", "download", "manifest", "electron", "win", "client", "feige", "pigeon", "doudian", "tos", "cdn")):
        interesting.append(s)
for s in sorted(set(interesting))[:80]:
    print(s[:260])

# tron context
for key in ("tron-client", "tronClient", "updateServer"):
    idx = 0
    n = 0
    while n < 8:
        idx = js.find(key, idx)
        if idx == -1:
            break
        print("CTX", key, js[max(0, idx-100):idx+250][:350])
        idx += len(key)
        n += 1
