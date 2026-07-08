"""Portable runtime discovery — bundled python/node next to exe or project root."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def project_root() -> Path:
    env = os.getenv("PIGEON_PROJECT_ROOT") or os.getenv("PIGEON_ROOT")
    if env:
        return Path(env)
    if (ROOT / "run.py").is_file():
        return ROOT
    exe = Path(sys.executable).resolve()
    for d in [exe.parent, *exe.parents]:
        if (d / "run.py").is_file():
            return d
    return ROOT


def runtime_dir() -> Path:
    return project_root() / "runtime"


def bundled_python() -> Path | None:
    for rel in (
        "runtime/python/python.exe",
        "runtime/python3/python.exe",
        "python/python.exe",
    ):
        p = project_root() / rel
        if p.is_file():
            return p
    return None


def bundled_node() -> Path | None:
    for rel in ("runtime/node/node.exe", "node/node.exe"):
        p = project_root() / rel
        if p.is_file():
            return p
    return None


def resolve_python() -> str:
    env = os.getenv("PIGEON_PYTHON")
    if env and Path(env).is_file():
        return env
    bundled = bundled_python()
    if bundled:
        return str(bundled)
    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found:
            return found
    return sys.executable or "python"


def resolve_node() -> str:
    env = os.getenv("PIGEON_NODE") or os.getenv("NODE_BIN")
    if env and Path(env).is_file():
        return env
    bundled = bundled_node()
    if bundled:
        return str(bundled)
    found = shutil.which("node")
    return found or "node"


def apply_runtime_env() -> dict[str, str]:
    """Set env vars so child processes use bundled runtimes."""
    root = str(project_root())
    updates = {
        "PIGEON_PROJECT_ROOT": root,
        "PIGEON_ROOT": root,
        "PIGEON_STANDALONE": os.getenv("PIGEON_STANDALONE", "1"),
        "PIGEON_WS_HOST": os.getenv("PIGEON_WS_HOST", "jinritemai"),
    }
    py = resolve_python()
    if py:
        updates["PIGEON_PYTHON"] = py
    node = resolve_node()
    if node and Path(node).is_file():
        updates["PIGEON_NODE"] = node
        node_dir = str(Path(node).parent)
        path = os.environ.get("PATH", "")
        if node_dir not in path:
            updates["PATH"] = node_dir + os.pathsep + path
    rt = runtime_dir()
    if rt.is_dir():
        for sub in ("python", "python3", "node"):
            p = rt / sub
            if p.is_dir() and str(p) not in os.environ.get("PATH", ""):
                updates["PATH"] = str(p) + os.pathsep + updates.get("PATH", os.environ.get("PATH", ""))
    for k, v in updates.items():
        os.environ[k] = v
    try:
        from pigeon_protocol.account_context import init_account_context

        init_account_context(migrate=True)
    except Exception:
        pass
    return updates


def chrome_executable() -> Path:
    env = os.getenv("PIGEON_CHROME_PATH")
    if env and Path(env).is_file():
        return Path(env)
    local = os.getenv("LOCALAPPDATA", "")
    if local:
        p = Path(local) / "Google/Chrome/Application/chrome.exe"
        if p.is_file():
            return p
    return Path("chrome.exe")


def chrome_profile_dir() -> Path:
    env = os.getenv("PIGEON_CHROME_PROFILE")
    if env:
        return Path(env)
    return project_root() / "runtime" / "chrome-profile"


def cdp_port() -> int:
    return int(os.getenv("CDP_PORT", "9222"))
