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
def registry_lock(*, timeout_sec: float = 3.0, block: bool = True):
    """Cross-process lock for node_pids.json read-modify-write."""
    _ = timeout_sec  # reserved for future timed retry tuning
    with _registry_file_lock(block=block):
        yield


def _cleanup_dead_registered_processes_unlocked(doc: dict[str, Any]) -> None:
    alive_rows: list[dict[str, Any]] = []
    for row in doc.get("processes") or []:
        if not isinstance(row, dict):
            continue
        pid = int(row.get("pid") or 0)
        if pid > 0 and _pid_alive(pid):
            alive_rows.append(row)
    doc["processes"] = alive_rows


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
        _cleanup_dead_registered_processes_unlocked(doc)
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
    with registry_lock():
        doc = _read_registry_unlocked()
        _cleanup_dead_registered_processes_unlocked(doc)
        _write_registry_unlocked(doc)
        rows = doc.get("processes") or []
    out: list[dict[str, Any]] = []
    for row in rows:
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
                "last_kill_failed_at": int(row.get("last_kill_failed_at") or 0),
                "last_kill_error": str(row.get("last_kill_error") or ""),
            }
        )
    return out


def cleanup_dead_registered_processes() -> None:
    _mutate_registry(_cleanup_dead_registered_processes_unlocked)


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


def kill_registered_processes(kind: str = "node", older_than_sec: int | None = None) -> dict[str, Any]:
    """Kill registered PIDs only — never scans arbitrary node.exe."""
    cleanup_dead_registered_processes()
    now = int(time.time())
    targets: list[dict[str, Any]] = []

    with _registry_file_lock(block=True):
        doc = _read_registry_unlocked()
        for row in doc.get("processes") or []:
            if not isinstance(row, dict):
                continue
            row_kind = str(row.get("kind") or "node")
            if kind and row_kind != kind:
                continue
            pid = int(row.get("pid") or 0)
            created = int(row.get("created_at") or 0)
            age = now - created if created else 0
            if older_than_sec is not None and age < int(older_than_sec):
                continue
            if pid > 0 and _pid_alive(pid):
                targets.append(dict(row))

    killed = 0
    failed = 0
    kill_results: dict[int, tuple[bool, str]] = {}
    for row in targets:
        pid = int(row.get("pid") or 0)
        if pid <= 0:
            continue
        _kill_pid(pid)
        if _pid_alive(pid):
            failed += 1
            kill_results[pid] = (False, "kill_failed_still_alive")
        else:
            killed += 1
            kill_results[pid] = (True, "")
            _local_registered.discard(pid)

    if kill_results:
        def _mut(doc: dict[str, Any]) -> None:
            keep: list[dict[str, Any]] = []
            for row in doc.get("processes") or []:
                if not isinstance(row, dict):
                    continue
                pid = int(row.get("pid") or 0)
                if pid not in kill_results:
                    keep.append(row)
                    continue
                success, err = kill_results[pid]
                if success or not _pid_alive(pid):
                    continue
                updated = dict(row)
                updated["last_kill_failed_at"] = now
                updated["last_kill_error"] = err
                keep.append(updated)
            doc["processes"] = keep

        _mutate_registry(_mut)

    if killed or failed:
        logger.info("kill_registered_processes kind=%s killed=%d failed=%d", kind, killed, failed)
    return {"ok": True, "killed": killed, "failed": failed}


def kill_actual_project_nodes_not_registered() -> dict[str, Any]:
    """Global cleanup only — kill project node.exe not present in registry."""
    registered = {int(r.get("pid") or 0) for r in list_registered_processes(kind="node")}
    pids, scan_ok = scan_actual_project_node_pids()
    killed_unregistered = 0
    if not scan_ok:
        return {"killed_unregistered": 0, "actual_scan_ok": False}

    for pid in pids:
        if pid in registered or pid <= 0 or not _pid_alive(pid):
            continue
        _kill_pid(pid)
        if not _pid_alive(pid):
            killed_unregistered += 1

    cleanup_dead_registered_processes()
    return {"killed_unregistered": killed_unregistered, "actual_scan_ok": True}


def kill_local_registered_processes(kind: str = "node") -> int:
    """Kill only PIDs registered by this Python process (_local_registered)."""
    pids = sorted(int(p) for p in list(_local_registered) if int(p) > 0)
    if not pids:
        return 0
    killed = 0
    dead_pids: set[int] = set()
    for pid in pids:
        if _pid_alive(pid):
            _kill_pid(pid)
        if not _pid_alive(pid):
            killed += 1
            dead_pids.add(pid)
            _local_registered.discard(pid)

    if dead_pids:

        def _mut(doc: dict[str, Any]) -> None:
            doc["processes"] = [p for p in doc.get("processes") or [] if int(p.get("pid") or 0) not in dead_pids]

        _mutate_registry(_mut)

    if killed:
        logger.info("kill_local_registered_processes kind=%s killed=%d", kind, killed)
    return killed


def kill_owned_registered_processes(kind: str = "node") -> int:
    """Backward-compatible alias — local cleanup only."""
    return kill_local_registered_processes(kind=kind)


def count_live_registered_processes(kind: str = "node") -> int:
    return sum(1 for row in list_registered_processes(kind=kind) if row.get("alive"))


def ensure_node_capacity() -> bool:
    cleanup_dead_registered_processes()
    live = count_live_registered_processes(kind="node")
    return live < node_process_limit()


def _cmdline_matches_project(cmdline: str) -> bool:
    text = str(cmdline or "")
    return any(marker in text for marker in PROJECT_CMD_MARKERS)


def scan_actual_project_node_pids() -> tuple[list[int], bool]:
    """Return live project node.exe PIDs and whether cmdline scan succeeded."""
    if os.name == "nt":
        pattern = "douyin-pigeon-protocol|run_bdms_daemon\\.mjs|run_bdms_fetch\\.mjs|pigeon_protocol|pigeon-feige"
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" -ErrorAction SilentlyContinue | "
            f"Where-Object {{ $_.CommandLine -match '{pattern}' }} | "
            "Select-Object -ExpandProperty ProcessId"
        )
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            pids: list[int] = []
            for line in (out.stdout or "").splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
            return sorted(set(pids)), True
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("scan_actual_project_node_pids failed: %s", exc)
            return [], False
    try:
        out = subprocess.run(["ps", "-ax", "-o", "pid=,command="], capture_output=True, text=True, timeout=5, check=False)
        pids = []
        for line in (out.stdout or "").splitlines():
            if "node" not in line.lower():
                continue
            if not _cmdline_matches_project(line):
                continue
            parts = line.strip().split(None, 1)
            if parts and parts[0].isdigit():
                pids.append(int(parts[0]))
        return sorted(set(pids)), True
    except (OSError, subprocess.TimeoutExpired):
        return [], False


def count_actual_project_live_nodes() -> tuple[int, bool]:
    pids, ok = scan_actual_project_node_pids()
    live = sum(1 for pid in pids if _pid_alive(pid))
    return live, ok


def process_status() -> dict[str, Any]:
    items = list_registered_processes(kind="node")
    live = sum(1 for x in items if x.get("alive"))
    actual_live, actual_ok = count_actual_project_live_nodes()
    mismatch = actual_ok and actual_live != live
    return {
        "ok": True,
        "node": {
            "max": node_process_limit(),
            "registered_live": live,
            "registered_total": len(items),
            "actual_project_live": actual_live if actual_ok else None,
            "actual_scan_ok": actual_ok,
            "mismatch": mismatch,
            "oneshot_fallback": oneshot_node_fallback_enabled(),
            "items": items,
        },
    }


def cleanup_stale_nodes(*, older_than_sec: int, reason: str = "") -> dict[str, Any]:
    cleanup_dead_registered_processes()
    report = kill_registered_processes(kind="node", older_than_sec=int(older_than_sec))
    report["mode"] = "stale"
    report["reason"] = reason
    return report


def cleanup_global_nodes(*, reason: str = "") -> dict[str, Any]:
    cleanup_dead_registered_processes()
    report = kill_registered_processes(kind="node", older_than_sec=None)
    extra = kill_actual_project_nodes_not_registered()
    release_bdms_daemon_lock()
    cleanup_dead_registered_processes()
    return {
        "ok": True,
        "killed": int(report.get("killed") or 0),
        "failed": int(report.get("failed") or 0),
        "killed_unregistered": int(extra.get("killed_unregistered") or 0),
        "actual_scan_ok": bool(extra.get("actual_scan_ok")),
        "mode": "global",
        "reason": reason,
    }


def process_cleanup(*, kill_all: bool = False, older_than_sec: int | None = None) -> dict[str, Any]:
    if kill_all:
        return cleanup_global_nodes(reason="process_cleanup")
    if older_than_sec is not None:
        return cleanup_stale_nodes(older_than_sec=int(older_than_sec), reason="process_cleanup")
    cleanup_dead_registered_processes()
    return {"ok": True, "killed": 0, "failed": 0, "mode": "noop"}


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
    killed = kill_local_registered_processes(kind="node")
    release_bdms_daemon_lock()
    return {"killed": killed, "reason": reason, "mode": "local"}


def cleanup_project_nodes(*, kill_all: bool = True, reason: str = "", older_than_sec: int | None = None) -> dict[str, Any]:
    """Bridge/EXE cleanup entry — routes to stale or global tier."""
    if kill_all and older_than_sec is None:
        return cleanup_global_nodes(reason=reason or "cleanup_project_nodes")
    if older_than_sec is not None:
        return cleanup_stale_nodes(older_than_sec=int(older_than_sec), reason=reason or "cleanup_project_nodes")
    cleanup_dead_registered_processes()
    return {"ok": True, "killed": 0, "mode": "noop", "reason": reason}


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
        kill_local_registered_processes(kind="node")
        release_bdms_daemon_lock()
    except Exception:
        pass


atexit.register(_atexit_cleanup)
