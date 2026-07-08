#!/usr/bin/env python3
import json
import re
import urllib.request
from pathlib import Path

js = Path(__file__).resolve().parents[1].joinpath("download_index.js").read_text(encoding="utf-8", errors="replace")

# all https strings
urls = sorted(set(re.findall(r"https?://[a-zA-Z0-9_./?=&%-]+", js)))
print("url count", len(urls))
for u in urls:
    if any(k in u.lower() for k in ("manifest", "update", "electron", "download", "client", "version", "pigeon", "im.", "tos", "exe")):
        print(u)

# manifest context
idx = js.find("manifest")
while idx != -1:
    print("CTX", js[max(0, idx - 150) : idx + 250])
    idx = js.find("manifest", idx + 1)
    if idx > 0 and js.count("manifest") > 20:
        break

# find .get("url") patterns
for m in re.finditer(r'\.get\(\s*"([^"]+)"', js):
    u = m.group(1)
    if "http" in u or "manifest" in u or "version" in u or "client" in u:
        print("GET", u)
