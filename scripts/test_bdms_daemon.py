#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.conv_list import _unsigned_url
from pigeon_protocol.foundation.bdms_node_daemon import close_daemon, sign_via_daemon
from pigeon_protocol.session import load_session

close_daemon()
s = load_session()
u = _unsigned_url(queue_key="no_order", page_size=20, session=s)
r = sign_via_daemon(u, method="GET")
print(json.dumps(r, ensure_ascii=False)[:500] if r else "None")
close_daemon()
