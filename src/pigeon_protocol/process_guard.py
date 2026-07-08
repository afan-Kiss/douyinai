"""Global guard for project-owned node.exe — registry, limits, cleanup."""
from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from pigeon_protocol.config import ROOT

logger = logging.getLogger("pigeon.process_guard")

PID_FILE_NAME = "node_pids.json"
REGISTRY_LOCK_FILE_NAME = "node_pids.lock"
DAEMON_LOCK_FILE_NAME = "bdms_daemon.lock"

PROJECT_CMD_MARKERS = (
    "douyin-pigeon-protocol",
    "run_bdms_daemon.mjs",
    "run_bdms_fetch.mjs",
    "pigeon_protocol",
    "pigeon-feige",
)

_local_registered: set[int] = set()
_daemon_lock_handle = None


class NodeProcessLimitError(RuntimeError):
    """Raised when spawning another node.exe would exceed the global limit."""


def runtime_dir() -> Path:
    path = ROOT / "logs" / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pid_file() -> Path:
    return runtime_dir() / PID_FILE_NAME


def _daemon_lock_file() -> Path:
    return runtime_dir() / DAEMON_LOCK_FILE_NAME


def node_process_limit() -> int:
    raw = os.getenv("PIGEON_NODE_MAX_PROCS", "2").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 2


def oneshot_node_fallback_enabled() -> bool:
    return os.getenv("PIGEON_NODE_ONESHOT_FALLBACK", "0").strip().lower() in ("1", "true", "yes", "on")


def _registry_lock_file() -> Path:
    return runtime_dir() / REGISTRY_LOCK_FILE_NAME


@contextlib.contextmanager
def _registry_file_lock(*, block: bool = True):
    path = _registry_lock_file()
    fh = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            mode = msvcrt.LK_LOCK if block else msvcrt.LK_NBLCK
            if not block:
                for _ in range(40):
                    try:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.025)
                else:
                    raise TimeoutError("node_pids registry lock timeout")
            else:
                msvcrt.locking(fh.fileno(), mode, 1)
        else:
            import fcntl

            flags = fcntl.LOCK_EX
            if not block:
                flags |= fcntl.LOCK_NB
            fcntl.flock(fh.fileno(), flags)
        yield
    finally:
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


def _read_registry_unlocked() -> dict[str, Any]:
    path = _pid_file()
    if not path.is_file():
        return {"processes": [], "updated_at": 0}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"processes": [], "updated_at": 0}
    if isinstance(doc.get("processes"), list):
        return doc
    # migrate legacy {"pids": {...}}
    processes: list[dict[str, Any]] = []
    for pid, meta in (doc.get("pids") or {}).items():
        if isinstance(meta, dict):
            processes.append(
                {
                    "pid": int(pid),
                    "kind": meta.get("kind") or "node",
                    "cmdline": meta.get("cmdline") or "",
                    "created_at": int(meta.get("started_at") or meta.get("created_at") or 0),
                    "owner_pid": int(meta.get("owner") or 0),
                }
            )
    return {"processes": processes, "updated_at": int(doc.get("updated_at") or 0)}


def _read_registry() -> dict[str, Any]:
    with _registry_file_lock(block=True):
        return _read_registry_unlocked()


def _write_registry_unlocked(doc: dict[str, Any]) -> None:
    doc["updated_at"] = int(time.time())
    _pid_file().write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_registry(doc: dict[str, Any]) -> None:
    with _registry_file_lock(block=True):
        _write_registry_unlocked(doc)


def _mutate_registry(mutator) -> None:
    with _registry_file_lock(block=True):
        doc = _read_registry_unlocked()
        mutator(doc)
        _write_registry_unlocked(doc)


def _cmdline_text(cmdline: list[str] | str) -> str:
    if isinstance(cmdline, list):
        return " ".join(str(x) for x in cmdline)
    return str(cmdline or "")


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
            text = ((out.stdout or "") + (out.stderr or "")).lower()
            return str(pid) in text and "node.exe" in text
        except (OSError, subprocess.TimeoutExpired):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def register_child_process(kind: str, pid: int, cmdline: list[str] | str) -> None:
    if pid <= 0:
        return

    def _mut(doc: dict[str, Any]) -> None:
        processes = [p for p in doc.get("processes") or [] if int(p.get("pid") or 0) != pid]
        processes.append(
            {
                "pid": int(pid),
                "kind": str(kind or "node"),
                "cmdline": _cmdline_text(cmdline),
                "created_at": int(time.time()),
                "owner_pid": os.getpid(),
            }
        )
        doc["processes"] = processes

    cleanup_dead_registered_processes()
    _mutate_registry(_mut)
    _local_registered.add(int(pid))


def unregister_child_process(pid: int) -> None:
    if pid <= 0:
        return

    def _mut(doc: dict[str, Any]) -> None:
        doc["processes"] = [p for p in doc.get("processes") or [] if int(p.get("pid") or 0) != pid]

    _mutate_registry(_mut)
    _local_registered.discard(int(pid))


def list_registered_processes(kind: str = "node") -> list[dict[str, Any]]:
    cleanup_dead_registered_processes()
    out: list[dict[str, Any]] = []
    for row in _read_registry().get("processes") or []:
        if not isinstance(row, dict):
            continue
        row_kind = str(row.get("kind") or "node")
        if kind and row_kind != kind:
            continue
        pid = int(row.get("pid") or 0)
        alive = _pid_alive(pid)
        out.append(
            {
                "pid": pid,
                "kind": row_kind,
                "cmdline": str(row.get("cmdline") or ""),
                "alive": alive,
                "created_at": int(row.get("created_at") or 0),
                "owner_pid": int(row.get("owner_pid") or 0),
            }
        )
    return out


def cleanup_dead_registered_processes() -> None:
    def _mut(doc: dict[str, Any]) -> None:
        alive_rows: list[dict[str, Any]] = []
        for row in doc.get("processes") or []:
            if not isinstance(row, dict):
                continue
            pid = int(row.get("pid") or 0)
            if pid > 0 and _pid_alive(pid):
                alive_rows.append(row)
        doc["processes"] = alive_rows

    _mutate_registry(_mut)


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


def kill_registered_processes(kind: str = "node", older_than_sec: int | None = None) -> int:
    """Kill registered PIDs only — never scans arbitrary node.exe."""
    cleanup_dead_registered_processes()
    now = int(time.time())
    killed = 0
    to_kill: list[int] = []

    with _registry_file_lock(block=True):
        doc = _read_registry_unlocked()
        keep: list[dict[str, Any]] = []
        for row in doc.get("processes") or []:
            if not isinstance(row, dict):
                continue
            row_kind = str(row.get("kind") or "node")
            if kind and row_kind != kind:
                keep.append(row)
                continue
            pid = int(row.get("pid") or 0)
            created = int(row.get("created_at") or 0)
            age = now - created if created else 0
            if older_than_sec is not None and age < int(older_than_sec):
                keep.append(row)
                continue
            if pid > 0 and _pid_alive(pid):
                to_kill.append(pid)
            _local_registered.discard(pid)
        doc["processes"] = keep
        _write_registry_unlocked(doc)

    for pid in to_kill:
        if _kill_pid(pid):
            killed += 1
    if killed:
        logger.info("kill_registered_processes kind=%s killed=%d", kind, killed)
    return killed


def kill_owned_registered_processes(kind: str = "node") -> int:
    """Kill node rows owned by this Python process only."""
    owner = os.getpid()
    killed = 0
    to_kill: list[int] = []

    with _registry_file_lock(block=True):
        doc = _read_registry_unlocked()
        keep: list[dict[str, Any]] = []
        for row in doc.get("processes") or []:
            if not isinstance(row, dict):
                continue
            row_kind = str(row.get("kind") or "node")
            pid = int(row.get("pid") or 0)
            row_owner = int(row.get("owner_pid") or 0)
            owned = row_owner == owner or pid in _local_registered
            if kind and row_kind == kind and owned and pid > 0 and _pid_alive(pid):
                to_kill.append(pid)
                _local_registered.discard(pid)
                continue
            keep.append(row)
        doc["processes"] = keep
        _write_registry_unlocked(doc)

    for pid in to_kill:
        if _kill_pid(pid):
            killed += 1
    if killed:
        logger.info("kill_owned_registered_processes kind=%s killed=%d owner=%d", kind, killed, owner)
    return killed


def count_live_registered_processes(kind: str = "node") -> int:
    return sum(1 for row in list_registered_processes(kind=kind) if row.get("alive"))


def ensure_node_capacity() -> bool:
    cleanup_dead_registered_processes()
    live = count_live_registered_processes(kind="node")
    return live < node_process_limit()


def process_status() -> dict[str, Any]:
    items = list_registered_processes(kind="node")
    live = sum(1 for x in items if x.get("alive"))
    return {
        "ok": True,
        "node": {
            "max": node_process_limit(),
            "registered_live": live,
            "registered_total": len(items),
            "oneshot_fallback": oneshot_node_fallback_enabled(),
            "items": items,
        },
    }


def process_cleanup(*, kill_all: bool = False, older_than_sec: int | None = None) -> dict[str, Any]:
    cleanup_dead_registered_processes()
    if kill_all:
        killed = kill_registered_processes(kind="node", older_than_sec=None)
    elif older_than_sec is not None:
        killed = kill_registered_processes(kind="node", older_than_sec=int(older_than_sec))
    else:
        killed = 0
    return {"ok": True, "killed": killed}


# --- bdms daemon cross-process lock ---


def acquire_bdms_daemon_lock() -> bool:
    global _daemon_lock_handle
    if _daemon_lock_handle is not None:
        return True
    path = _daemon_lock_file()
    try:
        fh = open(path, "a+b")
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
        _daemon_lock_file().unlink(missing_ok=True)
    except OSError:
        pass


def shutdown_local_nodes(*, reason: str = "") -> dict[str, Any]:
    try:
        from pigeon_protocol.foundation.bdms_node_daemon import close_daemon

        close_daemon()
    except Exception as exc:
        logger.debug("close bdms daemon: %s", exc)
    killed = kill_registered_processes(kind="node")
    release_bdms_daemon_lock()
    return {"killed": killed, "reason": reason}


def cleanup_project_nodes(*, kill_all: bool = True, reason: str = "", older_than_sec: int | None = None) -> dict[str, Any]:
    """Bridge/EXE cleanup entry."""
    if kill_all and older_than_sec is None:
        killed = kill_registered_processes(kind="node")
    elif older_than_sec is not None:
        killed = kill_registered_processes(kind="node", older_than_sec=older_than_sec)
    else:
        cleanup_dead_registered_processes()
        killed = 0
    release_bdms_daemon_lock()
    return {"ok": True, "killed": killed, "reason": reason}


# backward-compatible aliases
def register_node_pid(pid: int, *, kind: str = "node", owner: str = "") -> None:
    register_child_process(kind, pid, owner or kind)


def unregister_node_pid(pid: int) -> None:
    unregister_child_process(pid)


def prepare_node_spawn(*, kind: str = "node") -> tuple[bool, str]:
    if ensure_node_capacity():
        return True, ""
    live = count_live_registered_processes(kind="node")
    return False, f"node limit reached ({live}/{node_process_limit()})"


def _atexit_cleanup() -> None:
    try:
        from pigeon_protocol.foundation.bdms_node_daemon import close_daemon

        close_daemon()
    except Exception:
        pass
    try:
        kill_owned_registered_processes(kind="node")
        release_bdms_daemon_lock()
    except Exception:
        pass


atexit.register(_atexit_cleanup)
