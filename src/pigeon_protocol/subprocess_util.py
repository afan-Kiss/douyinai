"""Windows-friendly subprocess helpers — no flashing console windows."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def hidden_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": si}


def _argv0(args: tuple[Any, ...]) -> list[str]:
    if not args:
        return []
    first = args[0]
    if isinstance(first, (list, tuple)):
        return [str(x) for x in first]
    return [str(first)]


def _is_node_command(argv: list[str]) -> bool:
    if not argv:
        return False
    name = Path(str(argv[0])).name.lower()
    return name in ("node.exe", "node")


def _guard_node_spawn(argv: list[str]) -> None:
    if not _is_node_command(argv):
        return
    from pigeon_protocol.process_guard import NodeProcessLimitError, ensure_node_capacity

    if not ensure_node_capacity():
        raise NodeProcessLimitError("node process limit reached")


def run_hidden(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    argv = _argv0(args)
    _guard_node_spawn(argv)
    kwargs.update(hidden_kwargs())
    return subprocess.run(*args, **kwargs)


def popen_hidden(*args: Any, **kwargs: Any) -> subprocess.Popen:
    argv = _argv0(args)
    _guard_node_spawn(argv)
    kwargs.update(hidden_kwargs())
    return subprocess.Popen(*args, **kwargs)
