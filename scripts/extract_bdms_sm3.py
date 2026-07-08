"""Extract SM3 (be class) from bdms.js into standalone JS for testing."""
from pathlib import Path
import re

text = Path(__file__).resolve().parents[1].joinpath("analysis/bdms.js").read_text(encoding="utf-8")
idx = text.find("be=function(){function t()")
if idx < 0:
    idx = text.find("this.reg[0]=1937774191")
    idx = text.rfind("function", 0, idx)
chunk = text[idx : idx + 8000]
out = Path(__file__).resolve().parents[1] / "analysis" / "bdms_sm3_extract.js"
# wrap as module
wrapped = "const SM3 = " + chunk.split("},we=function")[0] + "};\nmodule.exports = SM3;\n"
out.write_text(wrapped, encoding="utf-8")
print("written", out, "len", len(wrapped))
print(wrapped[:500])
