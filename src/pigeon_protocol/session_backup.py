"""Session backup before destructive auth merges."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

MAX_BACKUPS = 8


def refresh_paths() -> None:
    """No-op placeholder — paths resolved dynamically via account_context."""


def _session_file() -> Path:
    from pigeon_protocol.account_context import session_file

    return session_file()


def _backup_dir() -> Path:
    from pigeon_protocol.account_context import backup_dir

    return backup_dir()


def backup_session(*, tag: str = "auto") -> dict[str, Any]:
    sf = _session_file()
    if not sf.is_file():
        return {"ok": False, "error": "no session file"}
    bdir = _backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    name = f"session_{tag}_{ts}.json"
    dest = bdir / name
    shutil.copy2(sf, dest)
    _prune_old()
    return {"ok": True, "path": str(dest), "tag": tag}


def restore_latest_backup(*, tag: str = "") -> dict[str, Any]:
    bdir = _backup_dir()
    if not bdir.is_dir():
        return {"ok": False, "error": "no backups"}
    files = sorted(bdir.glob("session_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if tag:
        files = [p for p in files if tag in p.name]
    if not files:
        return {"ok": False, "error": "no matching backup"}
    src = files[0]
    sf = _session_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, sf)
    return {"ok": True, "path": str(src)}


def _prune_old() -> None:
    bdir = _backup_dir()
    files = sorted(bdir.glob("session_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[MAX_BACKUPS:]:
        try:
            p.unlink()
        except OSError:
            pass
