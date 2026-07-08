"""Smoke tests for process_guard local vs global cleanup."""
from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PID_FILE = ROOT / "logs" / "runtime" / "node_pids.json"


def _env() -> dict[str, str]:
    import os

    return {
        **dict(os.environ),
        "PYTHONPATH": str(SRC),
        "PIGEON_ROOT": str(ROOT),
        "PIGEON_PROJECT_ROOT": str(ROOT),
    }


def _run_py(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=_env(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_registry_json_valid() -> None:
    if not PID_FILE.is_file():
        return
    json.loads(PID_FILE.read_text(encoding="utf-8"))


def test_concurrent_process_status() -> None:
    code = "from pigeon_protocol.process_guard import cleanup_dead_registered_processes, process_status; cleanup_dead_registered_processes(); print(process_status()['ok'])"
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = [pool.submit(_run_py, code) for _ in range(10)]
        for fut in as_completed(futs):
            proc = fut.result()
            assert proc.returncode == 0, proc.stderr
            assert "True" in (proc.stdout or "")


def test_oneshot_exit_does_not_global_kill() -> None:
    # Snapshot registered live before/after a one-shot process_status import+exit.
    before = _run_py("from pigeon_protocol.process_guard import process_status; print(process_status()['node']['registered_live'])")
    assert before.returncode == 0
    _run_py("from pigeon_protocol.process_guard import process_status; process_status()")
    after = _run_py("from pigeon_protocol.process_guard import process_status; print(process_status()['node']['registered_live'])")
    assert after.returncode == 0
    assert (before.stdout or "").strip() == (after.stdout or "").strip()


def main() -> int:
    test_registry_json_valid()
    test_concurrent_process_status()
    test_oneshot_exit_does_not_global_kill()
    print("process_guard smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
