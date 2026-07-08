#!/usr/bin/env python3
import re
from pathlib import Path

for name in ("download_index.js", "download_vendor.js"):
    js = Path(__file__).resolve().parents[1].joinpath(name).read_text(encoding="utf-8", errors="replace")
    print("===", name, "len", len(js))
    for key in (
        "checkForUpdate",
        "feedUrl",
        "downloadUrl",
        "downloadUrlX64",
        "electron-updater",
        "autoUpdater",
        "win32",
        "manifest",
        "pigeon",
        "feige",
        "doudian",
        "latest.yml",
        "latest-win",
    ):
        if key in js:
            print(" has", key, "count", js.count(key))
    for m in re.finditer(r"https?://[a-zA-Z0-9_./?=&%-]+", js):
        u = m.group()
        if any(k in u.lower() for k in ("update", "electron", "client", "manifest", "version", "download", "tos", "feige", "pigeon", "doudian")):
            print(" url", u[:260])
    for key in ("checkForUpdate", "feedUrl"):
        idx = 0
        shown = 0
        while shown < 5:
            idx = js.find(key, idx)
            if idx == -1:
                break
            print(" ctx", key, js[max(0, idx - 120) : idx + 200][:320])
            idx += len(key)
            shown += 1
