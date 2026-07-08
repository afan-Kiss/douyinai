#!/usr/bin/env python3
"""Export 7 canonical 169B inners → per-account bundle/ws_inner_canonical.json."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _out_path() -> Path:
    bundle = os.getenv("PIGEON_BUNDLE_DIR", "").strip()
    if bundle:
        return Path(bundle) / "ws_inner_canonical.json"
    return ROOT / "standalone_bundle" / "ws_inner_canonical.json"


def main() -> int:
    from pigeon_protocol.foundation.ws_blob_compute import (
        _load_session_class_cache,
        classify_inner,
        inner_class_registry,
        pool_inner_for_class,
        registry_report,
    )
    from pigeon_protocol.session import load_session

    OUT = _out_path()
    session = load_session()
    cached = _load_session_class_cache(session)
    reg = inner_class_registry()
    classes: list[dict] = []
    for ic in reg.values():
        inner = cached.get(ic.class_id) or pool_inner_for_class(ic)
        if not inner:
            continue
        classes.append(
            {
                "class_id": ic.class_id,
                "text_b": ic.text_b,
                "inner_hex": inner.hex(),
                "label": ic.label,
            }
        )

    from pigeon_protocol.foundation.ws_session_inner import _session_key

    payload = {
        "session_key": _session_key(session),
        "class_count": len(classes),
        "classes": classes,
        "registry": registry_report(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(OUT), "classes": len(classes)}, ensure_ascii=False))
    return 0 if classes else 1


if __name__ == "__main__":
    raise SystemExit(main())
