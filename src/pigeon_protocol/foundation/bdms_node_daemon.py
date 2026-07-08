"""Persistent Node bdms daemon — amortize jsdom+bdms.init cost."""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from pigeon_protocol.subprocess_util import popen_hidden

logger = logging.getLogger("pigeon.bdms_daemon")

ROOT = Path(__file__).resolve().parents[3]
DAEMON_SCRIPT = ROOT / "scripts" / "run_bdms_daemon.mjs"

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_seq = 0
_ready = False


def _reset() -> None:
    global _proc, _ready
    if _proc and _proc.poll() is None:
        try:
            _proc.kill()
        except OSError:
            pass
    _proc = None
    _ready = False


def _ensure_daemon() -> bool:
    global _proc, _ready
    if not DAEMON_SCRIPT.is_file():
        return False
    if _proc and _proc.poll() is None and _ready:
        return True
    _reset()
    try:
        _proc = popen_hidden(
            ["node", str(DAEMON_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(ROOT),
        )
    except OSError as exc:
        logger.debug("bdms daemon start failed: %s", exc)
        return False

    def _drain_stderr() -> None:
        if not _proc or not _proc.stderr:
            return
        for line in _proc.stderr:
            if "[bdms-daemon] ready" in line:
                global _ready
                _ready = True
            logger.debug("bdms-daemon: %s", line.rstrip())

    threading.Thread(target=_drain_stderr, daemon=True, name="bdms-daemon-stderr").start()
    deadline = time.time() + 15.0
    while time.time() < deadline:
        if _ready:
            return True
        if _proc.poll() is not None:
            break
        time.sleep(0.05)
    return _ready


def sign_via_daemon(unsigned_url: str, *, body: str = "", method: str = "GET", timeout_sec: float = 30.0) -> dict[str, Any] | None:
    global _seq
    with _lock:
        if not _ensure_daemon() or not _proc or not _proc.stdin or not _proc.stdout:
            return None
        _seq += 1
        req_id = _seq
        try:
            _proc.stdin.write(json.dumps({"id": req_id, "url": unsigned_url, "body": body, "method": method}) + "\n")
            _proc.stdin.flush()
        except OSError:
            _reset()
            return None

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if _proc.poll() is not None:
                _reset()
                return None
            line = _proc.stdout.readline()
            if not line.strip():
                continue
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue
            if resp.get("id") == req_id:
                return resp
    return None


def close_daemon() -> None:
    with _lock:
        _reset()
