#!/usr/bin/env python3
import re
import urllib.request

u = "https://lf3-fe.ecombdstatic.com/obj/ecom-cdn-default/ecom-dianshang-im/ecom_im_other/pages/download/index.50f17304.js"
js = urllib.request.urlopen(u, timeout=30).read().decode("utf-8", "replace")

for pat in (
    r"https?://[^\s\"']+manifest[^\s\"']*",
    r"https?://[^\s\"']+electron[^\s\"']*",
    r"https?://[^\s\"']+update[^\s\"']*",
    r"https?://[^\s\"']+pigeon[^\s\"']*",
    r"https?://[^\s\"']+im[^\s\"']*",
):
    ms = sorted(set(re.findall(pat, js, re.I)))
    if ms:
        print("===", pat)
        for m in ms[:30]:
            print(m[:260])

# find API get calls near manifest
for m in re.finditer(r".{0,120}manifest.{0,120}", js):
    s = m.group()
    if "http" in s or ".get(" in s or "fetch" in s:
        print("manifest ctx:", s[:300])
