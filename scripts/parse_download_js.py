#!/usr/bin/env python3
import re
import urllib.request

u = "https://lf3-fe.ecombdstatic.com/obj/ecom-cdn-default/ecom-dianshang-im/ecom_im_other/pages/download/index.50f17304.js"
js = urllib.request.urlopen(u, timeout=30).read().decode("utf-8", "replace")
print("len", len(js))
for pat in (
    r"https?://[^\s\"']+\.exe[^\s\"']*",
    r"https?://[^\s\"']*(?:win|windows|setup|installer|download|tos)[^\s\"']*",
    r"win(?:Url|Download|Link)\s*[:=]\s*[\"']([^\"']+)",
    r"mac(?:Url|Download|Link)\s*[:=]\s*[\"']([^\"']+)",
):
    ms = sorted(set(re.findall(pat, js, re.I)))
    print("pat", pat, "count", len(ms))
    for m in ms[:20]:
        print(" ", m[:240])

for m in re.finditer(r".{0,100}(?:win|mac|download|setup|exe).{0,100}", js, re.I):
    s = m.group()
    if "http" in s or ".exe" in s or "Url" in s:
        print("ctx", s[:240])
