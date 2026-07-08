from pathlib import Path
import re

text = Path(__file__).resolve().parents[1].joinpath("analysis/bdms.js").read_text(encoding="utf-8")
# J and Q must be between start of IIFE and W - search in bdms bundle tail only
start = text.find("var I,q,F,U,M,N,D,V,H,G,z=[],Y=[]")
chunk = text[start : start + 200000]
for name in ["J", "Q", "m", "d"]:
    for pat in [f"function {name}(", f",{name}=function(", f"var {name}=function("]:
        idx = 0
        while True:
            i = chunk.find(pat, idx)
            if i < 0:
                break
            print(f"\n=== {pat} at {start + i} ===")
            print(chunk[i : i + 600])
            idx = i + 1
