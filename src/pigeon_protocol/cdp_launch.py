"""Ensure Feige Chrome CDP is running (auto-launch when absent)."""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("pigeon.cdp_launch")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHROME = Path(os.getenv("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"
DEFAULT_PROFILE = Path(os.getenv("PIGEON_CHROME_PROFILE", r"D:\douyin-customer-assistant\data\chrome-profile"))
FEIGE_WORKSPACE = "https://im.jinritemai.com/pc_seller_v2/main/workspace"


def cdp_port() -> int:
    return int(os.getenv("CDP_PORT", "9222"))


def cdp_ready(port: int | None = None) -> bool:
    from pigeon_protocol.cdp_bridge import cdp_ready as _ready

    return _ready(port or cdp_port())


def launch_feige_chrome(
    *,
    port: int | None = None,
    profile: Path | None = None,
    url: str = FEIGE_WORKSPACE,
    wait_sec: float = 30.0,
) -> bool:
    """Start Chrome with remote debugging + Feige profile."""
    port = port or cdp_port()
    profile = profile or DEFAULT_PROFILE
    chrome = Path(os.getenv("PIGEON_CHROME_PATH", str(DEFAULT_CHROME)))
    if not chrome.is_file():
        logger.error("Chrome not found: %s", chrome)
        return False

    profile.mkdir(parents=True, exist_ok=True)
    args = [
        str(chrome),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    logger.info("launching Chrome CDP port=%s profile=%s", port, profile)
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if cdp_ready(port):
            return True
        time.sleep(1.0)
    return False


def ensure_cdp_ready(*, launch: bool = True, wait_sec: float = 30.0) -> bool:
    """Return True when CDP endpoint responds; optionally launch Chrome."""
    port = cdp_port()
    if cdp_ready(port):
        return True
    if not launch:
        return False

    ps1 = ROOT / "scripts" / "start_feige_cdp.ps1"
    if ps1.is_file():
        try:
            subprocess.run(  # noqa: S603
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
                check=False,
                timeout=int(wait_sec) + 5,
                capture_output=True,
                text=True,
            )
            if cdp_ready(port):
                return True
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("start_feige_cdp.ps1 failed: %s", exc)

    return launch_feige_chrome(port=port, wait_sec=wait_sec)
