#!/usr/bin/env python3
import re
from pathlib import Path

js = Path(__file__).resolve().parents[1].joinpath("download_vendor.js").read_text(encoding="utf-8", errors="replace")

for key in ("tron", "buildId", "getbuildId", "pid", "manifest", "post(P", "ies/tron", "checker"):
    idx = 0
    n = 0
    while n < 6:
        idx = js.find(key, idx)
        if idx == -1:
            break
        print("===", key)
        print(js[max(0, idx - 80) : idx + 400][:480])
        idx += len(key)
        n += 1

# find variable P assignment near tron
m = re.search(r"post\(P,\{pid", js)
if m:
    start = max(0, m.start() - 2000)
    chunk = js[start : m.start() + 200]
    # find P= in chunk
    for pm in re.finditer(r",P=\"([^\"]+)\"|P=\"([^\"]+)\"|var P=\"([^\"]+)\"", chunk):
        print("P assign", pm.group())

# search for tron API host patterns
for u in sorted(set(re.findall(r"https?://[a-zA-Z0-9_./?=&%-]+", js))):
    if any(k in u.lower() for k in ("tron", "byte", "ies", "update", "electron", "build", "manifest", "client")):
        print("URL", u)
