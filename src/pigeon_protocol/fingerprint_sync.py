"""Sync analysis/browser_fingerprint.json from live session — offline sign parity."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("pigeon.fp_sync")

ROOT = Path(__file__).resolve().parents[2]
FP_FILE = ROOT / "analysis" / "browser_fingerprint.json"


def sync_fingerprint_from_session(session) -> dict[str, Any]:
    """Align browser_fingerprint.json UA + s_v_web_id with session (Node bdms reads this)."""
    if not session:
        return {"ok": False, "error": "no session"}

    ua = str(getattr(session, "user_agent", "") or "")
    s_v = str((getattr(session, "cookies", None) or {}).get("s_v_web_id") or "")
    if not ua and not s_v:
        return {"ok": False, "error": "no ua or s_v_web_id"}

    fp: dict[str, Any] = {}
    if FP_FILE.is_file():
        try:
            fp = json.loads(FP_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            fp = {}

    changed: list[str] = []
    if ua and fp.get("ua") != ua:
        fp["ua"] = ua
        changed.append("ua")
    if s_v and fp.get("s_v_web_id") != s_v:
        fp["s_v_web_id"] = s_v
        changed.append("s_v_web_id")
        # Drop stale precomputed fp so bdms_abogus rebuilds from canvas template
        fp.pop("browser_fp", None)

    if not changed:
        return {"ok": True, "changed": [], "path": str(FP_FILE)}

    FP_FILE.parent.mkdir(parents=True, exist_ok=True)
    FP_FILE.write_text(json.dumps(fp, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("browser_fingerprint synced: %s", changed)
    return {"ok": True, "changed": changed, "path": str(FP_FILE)}
