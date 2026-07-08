#!/usr/bin/env python3
"""True cold-start: wipe inner cache, restore portable sidecar only, send."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("PIGEON_NO_RUST", "1")
os.environ.setdefault("PIGEON_NO_CDP", "1")
os.environ.setdefault("PIGEON_WS_HOST", "jinritemai")

CACHE = ROOT / "session" / "ws_inner_cache.json"
PORTABLE = ROOT / "session" / "ws_inner_portable.json"
BACKUP = ROOT / "session" / "ws_inner_cache.backup.json"


def main() -> int:
    from pigeon_protocol.foundation.ws_inner_edbx import derive_edbx_inner_session, ingest_derived_inners
    from pigeon_protocol.foundation.ws_inner_health import session_inner_health
    from pigeon_protocol.session import load_session, save_session
    from pigeon_protocol.session_portable import _ingest_inner_doc, _read_json, restore_portable_inners

    if CACHE.is_file():
        shutil.copy2(CACHE, BACKUP)

    session = load_session()
    session.extra = {k: v for k, v in (session.extra or {}).items() if not str(k).startswith("edbx_")}
    save_session(session)

    CACHE.write_text("{}", encoding="utf-8")

    doc = _read_json(PORTABLE) or {}
    report: dict = {"portable_edbx": doc.get("edbx"), "steps": ["cache_cleared"]}

    applied = _ingest_inner_doc(session, doc, source="cold_sim", trust_pack=True)
    report["ingested_classes"] = len(applied)
    report["steps"].append(f"ingest:{len(applied)}")

    restore = restore_portable_inners(session, trust_pack=True)
    report["restore"] = restore

    inner, derive = derive_edbx_inner_session(session)
    report["derive"] = derive
    if inner:
        ingest_derived_inners(session, inner, source="cold_derive")
        report["steps"].append("cold_derive")

    health = session_inner_health(session)
    report["send_ready"] = bool(health.get("ready"))

    if report["send_ready"]:
        from pigeon_protocol.foundation.rust_sdk_inner import resolve_conversation_id
        from pigeon_protocol.send import SendService

        route, _ = resolve_conversation_id(session)
        uid = route.split(":", 1)[0].lstrip("n") if route else ""
        sender = SendService(session, dry_run=False)
        result = sender.send_text("冷启动纯协议探针", security_user_id=uid, auto_harvest=False)
        report["send"] = {
            "ok": bool(result.ok),
            "mode": result.mode,
            "reason": result.reason or "",
            "inner_header": derive.get("header_hex"),
        }
    else:
        report["send"] = {"ok": False, "reason": "not send_ready"}

    if BACKUP.is_file():
        shutil.copy2(BACKUP, CACHE)

    out = ROOT / "analysis" / "cold_start_simulation.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    save_session(session)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("send", {}).get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
