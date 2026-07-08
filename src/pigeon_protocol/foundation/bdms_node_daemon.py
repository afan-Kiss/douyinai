"""Persistent Node bdms daemon — project-wide singleton (max 1)."""
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
START_FAIL_COOLDOWN_SEC = 10.0

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_seq = 0
_ready = False
_last_start_failed_at = 0.0


def _mark_start_failed() -> None:
    global _last_start_failed_at
    _last_start_failed_at = time.time()


def _in_start_cooldown() -> bool:
    return (time.time() - _last_start_failed_at) < START_FAIL_COOLDOWN_SEC


def _kill_proc() -> None:
    global _proc, _ready
    from pigeon_protocol.process_guard import cleanup_dead_registered_processes, unregister_child_process

    pid = 0
    if _proc and _proc.poll() is None:
        pid = int(_proc.pid or 0)
        try:
            _proc.kill()
        except OSError:
            pass
        try:
            _proc.wait(timeout=3)
        except (subprocess.TimeoutExpired, OSError):
            pass
    if pid:
        unregister_child_process(pid)
    _proc = None
    _ready = False
    cleanup_dead_registered_processes()


def _reset() -> None:
    _kill_proc()
    try:
        from pigeon_protocol.process_guard import release_bdms_daemon_lock

        release_bdms_daemon_lock()
    except Exception:
        pass


def _ensure_daemon() -> bool:
    global _proc, _ready
    if not DAEMON_SCRIPT.is_file():
        return False
    if _proc and _proc.poll() is None and _ready:
        return True
    if _in_start_cooldown():
        logger.debug("bdms daemon start skipped (cooldown)")
        return False

    from pigeon_protocol.process_guard import (
        NodeProcessLimitError,
        acquire_bdms_daemon_lock,
        ensure_node_capacity,
        register_child_process,
    )

    if not ensure_node_capacity():
        logger.warning("node process limit reached, skip bdms daemon start")
        _mark_start_failed()
        return False
    if not acquire_bdms_daemon_lock():
        logger.info("bdms daemon lock held by another process")
        _mark_start_failed()
        return False

    _kill_proc()
    cmd = ["node", str(DAEMON_SCRIPT)]
    try:
        _proc = popen_hidden(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(ROOT),
        )
    except NodeProcessLimitError as exc:
        logger.warning("bdms daemon spawn blocked: %s", exc)
        from pigeon_protocol.process_guard import release_bdms_daemon_lock

        release_bdms_daemon_lock()
        _mark_start_failed()
        return False
    except OSError as exc:
        logger.debug("bdms daemon start failed: %s", exc)
        from pigeon_protocol.process_guard import release_bdms_daemon_lock

        release_bdms_daemon_lock()
        _mark_start_failed()
        return False

    if _proc and _proc.pid:
        register_child_process("node", int(_proc.pid), cmd)

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
        if _proc and _proc.poll() is not None:
            break
        time.sleep(0.05)
    _reset()
    _mark_start_failed()
    return False


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
            _mark_start_failed()
            return None

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if _proc.poll() is not None:
                _reset()
                _mark_start_failed()
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
        _mark_start_failed()
    return None


def close_daemon() -> None:
    with _lock:
        _reset()
        try:
            from pigeon_protocol.process_guard import cleanup_dead_registered_processes

            cleanup_dead_registered_processes()
        except Exception:
            pass
