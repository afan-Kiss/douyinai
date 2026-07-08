"""Live verify xundan_chat_list conv list relay."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.conv_list import list_conversations_relay, parse_conversation_items
from pigeon_protocol.session import load_session


def main() -> int:
    raw = list_conversations_relay(load_session(), size=20)
    items = raw.get("items") or parse_conversation_items(raw)
    print(
        json.dumps(
            {
                "ok": bool(raw.get("ok") and items),
                "via": raw.get("via"),
                "count": len(items),
                "sample": items[:3],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if items else 1


if __name__ == "__main__":
    raise SystemExit(main())
