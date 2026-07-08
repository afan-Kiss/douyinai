from pathlib import Path
import re

text = Path(__file__).resolve().parents[1].joinpath("analysis/bdms.js").read_text(encoding="utf-8")
idx = text.find('("UEsCA')
i = idx + 2
while i < len(text) and text[i] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=":
    i += 1
print("=== 400 chars BEFORE b64 close ===")
print(text[i - 400 : i + 200])

# find all function X( between z=[],Y=[] and end of b64
start = text.find("z=[],Y=[]")
chunk = text[start:i + 500]
for m in re.finditer(r"function ([A-Z])\(([^)]*)\)\{", chunk):
    pos = m.start()
    print(f"\n--- function {m.group(1)}({m.group(2)}) ---")
    print(chunk[pos : pos + 500])
