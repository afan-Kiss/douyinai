#!/usr/bin/env python3
from curl_cffi import requests as cr
import re

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
url = "https://lf3-fe.ecombdstatic.com/obj/ecom-cdn-default/doudian/ffa-homepage/index.542a6ee0.js"
js = cr.get(url, impersonate="chrome131", headers={"User-Agent": UA}, timeout=30).text
for pat in ("get_qrcode", "check_qrconnect", "passport/web", "aid:", "service:", "4272", "1383", "2562"):
    idx = 0
    found = 0
    while found < 5:
        i = js.find(pat, idx)
        if i < 0:
            break
        print(f"--- {pat} @ {i} ---")
        print(js[max(0, i - 120) : i + 200])
        idx = i + len(pat)
        found += 1
