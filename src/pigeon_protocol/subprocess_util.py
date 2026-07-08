"""Windows-friendly subprocess helpers — no flashing console windows."""
from __future__ import annotations

import os
import subprocess
from typing import Any

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def hidden_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": si}


def run_hidden(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    kwargs.update(hidden_kwargs())
    return subprocess.run(*args, **kwargs)


def popen_hidden(*args: Any, **kwargs: Any) -> subprocess.Popen:
    kwargs.update(hidden_kwargs())
    return subprocess.Popen(*args, **kwargs)
