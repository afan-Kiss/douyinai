#!/usr/bin/env python3
"""Parse TtnetWsError from feige_push_body.bin."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
body_path = ROOT / "analysis" / "feige_push_body.bin"
raw = body_path.read_bytes().decode("latin-1", errors="replace")
i = raw.find("TtnetWsError")
if i < 0:
    print("no TtnetWsError")
    sys.exit(1)
chunk = raw[i : i + 12000]
qstart = chunk.find('("')
if qstart < 0:
    print("no quoted json start")
    sys.exit(1)
qend = chunk.find('")', qstart + 2)
if qend < 0:
    print("no quoted json end")
    sys.exit(1)
inner = chunk[qstart + 2 : qend]
inner = inner.encode("latin-1").decode("unicode_escape")
obj = json.loads(inner)
print("code:", obj.get("code"))
print("message:", obj.get("message"))
rl = obj.get("request_log", "")
if isinstance(rl, str):
    rlj = json.loads(rl)
    print("request_log_keys:", sorted(rlj.keys()))
    print(json.dumps(rlj, indent=2)[:4000])
