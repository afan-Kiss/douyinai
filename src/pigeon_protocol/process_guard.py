"""Global guard for project-owned node.exe — limit spawns and cleanup orphans."""
from __future__ import annotations

import atexit
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from pigeon_protocol.config import ROOT

logger = logging.getLogger("pigeon.process_guard")

MAX_NODE_PROCESSES = max(1, int(os.getenv("PIGEON_MAX_NODE", "1") or "1"))
GUARD_DIR = ROOT / "logs" / "runtime"
PID_FILE = GUARD_DIR / "node_pids.json"
DAEMON_STATE_FILE = GUARD_DIR / "bdms_daemon.json"
DAEMON_LOCK_FILE = GUARD_DIR / "bdms_daemon.lock"

PROJECT_MARKERS = (
    "run_bdms_daemon.mjs",
    "run_bdms_fetch.mjs",
    "run_bdms_node.mjs",
    "run_bdms_env.mjs",
    "run_frontier_glue.mjs",
    "run_frontier_sign.mjs",
    "douyin-pigeon-protocol",
    "pigeon-feige",
    "pigeon_protocol",
)

_local_registered: set[int] = set()
_daemon_lock_handle = None


class NodeProcessLimitError(RuntimeError):
    """Raised when spawning another node.exe would exceed the global limit."""


def _ensure_guard_dir() -> None:
    GUARD_DIR.mkdir(parents=True, exist_ok=True)


def _read_pid_registry() -> dict[str, Any]:
    _ensure_guard_dir()
    if not PID_FILE.is_file():
        return {"pids": {}, "updated_at": 0}
    try:
        doc = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"pids": {}, "updated_at": 0}
    doc.setdefault("pids", {})
    return doc


def _write_pid_registry(doc: dict[str, Any]) -> None:
    _ensure_guard_dir()
    doc["updated_at"] = int(time.time())
    PID_FILE.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            text = (out.stdout or "") + (out.stderr or "")
            return str(pid) in text and "node.exe" in text.lower()
        except (OSError, subprocess.TimeoutExpired):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _prune_registry(doc: dict[str, Any]) -> dict[str, Any]:
    pids = doc.get("pids") or {}
    alive: dict[str, Any] = {}
    for key, meta in pids.items():
        try:
            pid = int(key)
        except (TypeError, ValueError):
            continue
        if _pid_alive(pid):
            alive[str(pid)] = meta
    doc["pids"] = alive
    return doc


def register_node_pid(pid: int, *, kind: str = "node", owner: str = "") -> None:
    if pid <= 0:
        return
    doc = _prune_registry(_read_pid_registry())
    doc["pids"][str(pid)] = {
        "kind": kind,
        "owner": owner or str(os.getpid()),
        "started_at": int(time.time()),
    }
    _write_pid_registry(doc)
    _local_registered.add(pid)


def unregister_node_pid(pid: int) -> None:
    if pid <= 0:
        return
    doc = _read_pid_registry()
    doc.get("pids", {}).pop(str(pid), None)
    _write_pid_registry(doc)
    _local_registered.discard(pid)


def alive_project_node_count() -> int:
    doc = _prune_registry(_read_pid_registry())
    _write_pid_registry(doc)
    return len(doc.get("pids") or {})


def _command_line_is_project(cmdline: str) -> bool:
    text = str(cmdline or "")
    if not text:
        return False
    low = text.lower()
    root = str(ROOT).lower()
    if root and root in low:
        return True
    return any(marker.lower() in low for marker in PROJECT_MARKERS)


def _list_windows_node_processes() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
        "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        raw = (out.stdout or "").strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        rows: list[dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            pid = int(row.get("ProcessId") or 0)
            cmd = str(row.get("CommandLine") or "")
            if pid > 0:
                rows.append({"pid": pid, "cmdline": cmd})
        return rows
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as exc:
        logger.debug("list node processes: %s", exc)
        return []


def _kill_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid), "/T"],
                capture_output=True,
                timeout=8,
                check=False,
            )
        else:
            os.kill(pid, 9)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def cleanup_project_nodes(*, kill_all: bool = True, reason: str = "") -> dict[str, Any]:
    """Kill tracked and orphan project node.exe processes."""
    _ensure_guard_dir()
    report: dict[str, Any] = {"killed": [], "reason": reason, "alive_before": alive_project_node_count()}
    keep: set[int] = set()

    if kill_all:
        for row in _list_windows_node_processes():
            pid = int(row.get("pid") or 0)
            cmd = str(row.get("cmdline") or "")
            if pid <= 0 or pid in keep:
                continue
            if _command_line_is_project(cmd):
                if _kill_pid(pid):
                    report["killed"].append(pid)
                unregister_node_pid(pid)

    doc = {"pids": {}, "updated_at": int(time.time())}
    _write_pid_registry(doc)

    stale_lock = DAEMON_LOCK_FILE
    if stale_lock.exists() and kill_all:
        try:
            stale_lock.unlink(missing_ok=True)
        except OSError:
            pass
    if DAEMON_STATE_FILE.exists() and kill_all:
        try:
            DAEMON_STATE_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    global _daemon_lock_handle
    if _daemon_lock_handle is not None:
        try:
            _daemon_lock_handle.close()
        except OSError:
            pass
        _daemon_lock_handle = None

    report["alive_after"] = alive_project_node_count()
    if report["killed"]:
        logger.info("node cleanup killed %s (%s)", report["killed"], reason or "cleanup")
    return report


def prepare_node_spawn(*, kind: str = "node") -> tuple[bool, str]:
    """Return (allowed, reason). Refuses when global node limit reached."""
    _prune_registry(_read_pid_registry())
    alive = alive_project_node_count()
    if alive >= MAX_NODE_PROCESSES:
        return False, f"node limit reached ({alive}/{MAX_NODE_PROCESSES})"
    return True, ""


def acquire_bdms_daemon_lock() -> bool:
    """Cross-process lock — only one Python process may own the bdms node daemon."""
    global _daemon_lock_handle
    if _daemon_lock_handle is not None:
        return True
    _ensure_guard_dir()
    try:
        fh = open(DAEMON_LOCK_FILE, "a+b")
    except OSError as exc:
        logger.debug("daemon lock open failed: %s", exc)
        return False
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fh.close()
            return False
    else:
        import fcntl

        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
    _daemon_lock_handle = fh
    return True


def release_bdms_daemon_lock() -> None:
    global _daemon_lock_handle
    if _daemon_lock_handle is None:
        return
    fh = _daemon_lock_handle
    _daemon_lock_handle = None
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        fh.close()
    except OSError:
        pass
    try:
        DAEMON_LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        DAEMON_STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def write_bdms_daemon_state(*, node_pid: int) -> None:
    _ensure_guard_dir()
    doc = {
        "python_pid": os.getpid(),
        "node_pid": node_pid,
        "started_at": int(time.time()),
    }
    DAEMON_STATE_FILE.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def shutdown_local_nodes(*, reason: str = "") -> dict[str, Any]:
    """Best-effort shutdown for this Python process."""
    try:
        from pigeon_protocol.foundation.bdms_node_daemon import close_daemon

        close_daemon()
    except Exception as exc:
        logger.debug("close bdms daemon: %s", exc)
    killed = []
    for pid in list(_local_registered):
        if _kill_pid(pid):
            killed.append(pid)
        unregister_node_pid(pid)
    release_bdms_daemon_lock()
    return {"killed": killed, "reason": reason}


def _atexit_cleanup() -> None:
    try:
        shutdown_local_nodes(reason="atexit")
    except Exception:
        pass


atexit.register(_atexit_cleanup)
