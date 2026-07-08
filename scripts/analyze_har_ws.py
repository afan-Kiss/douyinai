#!/usr/bin/env python3
"""Quick HAR WS send analysis."""
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.ws_sign import locate_signature_region


def main() -> int:
    har = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\1\Desktop\im.jinritemai.com.har")
    data = json.loads(har.read_text(encoding="utf-8"))
    entries = (data.get("log") or {}).get("entries") or []
    ws_sends = signed = 0
    rows = []
    for i, e in enumerate(entries):
        for m in e.get("_webSocketMessages") or []:
            if m.get("type") != "send":
                continue
            ws_sends += 1
            d = m.get("data") or ""
            try:
                raw = base64.b64decode(d) if d else b""
            except Exception:
                raw = d.encode("latin-1") if isinstance(d, str) else bytes(d)
            has_sig = bool(locate_signature_region(raw)) if len(raw) > 500 else False
            if has_sig:
                signed += 1
            rows.append({"entry": i, "len": len(raw), "signed": has_sig, "opcode": m.get("opcode")})
    print(
        json.dumps(
            {
                "har": str(har),
                "entries": len(entries),
                "ws_send": ws_sends,
                "signed_send": signed,
                "log_cookies": len((data.get("log") or {}).get("cookies") or []),
                "samples": rows[:30],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
