from pathlib import Path
import re

text = Path(__file__).resolve().parents[1].joinpath("analysis/bdms.js").read_text(encoding="utf-8")
idx = text.find("function X(t,e,r,n){")
chunk = text[idx : idx + 12000]
Path(__file__).resolve().parents[1].joinpath("analysis/vm_executor_x.js").write_text(chunk, encoding="utf-8")
print("written", len(chunk), "chars")
# extract t===N or N===t patterns
for m in re.finditer(r"(\d+)===t\)", chunk):
    print("op", m.group(1))
