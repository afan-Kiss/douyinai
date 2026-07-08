#!/usr/bin/env python3
"""Verify init body patch + live get_message_by_init."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from pigeon_protocol.feige_init import _load_init_bytes, _patch_init_body, _post_get_message_by_init
    from pigeon_protocol.init_body import validate_init_body
    from pigeon_protocol.session import load_session

    session = load_session()
    raw = _load_init_bytes()
    patched = _patch_init_body(raw, session)
    v = validate_init_body(patched)
    print("patch valid:", v.get("ok"), "size:", len(patched))

    result = _post_get_message_by_init(session)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("init_ok") or result.get("body_len", 0) > 500 else 1


if __name__ == "__main__":
    raise SystemExit(main())
