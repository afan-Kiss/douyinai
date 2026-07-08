"""CDP browser onboard — login + cookie/169B capture + optional pack export."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from pigeon_protocol.config import IM_HOST
from pigeon_protocol.runtime_paths import (
    apply_runtime_env,
    cdp_port,
    chrome_executable,
    chrome_profile_dir,
    project_root,
)

logger = logging.getLogger("pigeon.cdp_onboard")

IM_WORKSPACE = f"{IM_HOST}/pc_seller_v2/main/workspace"

_job_lock = threading.Lock()
_job: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "error": "",
    "send_ready": False,
    "listen_ready": False,
    "blockers": [],
    "report": {},
}


def job_snapshot() -> dict[str, Any]:
    with _job_lock:
        return dict(_job)


def _set_job(**kwargs: Any) -> None:
    with _job_lock:
        _job.update(kwargs)


def _cdp_ready(port: int | None = None) -> bool:
    from pigeon_protocol.cdp_launch import cdp_ready

    return cdp_ready(port or cdp_port())


def _launch_chrome(*, port: int | None = None) -> subprocess.Popen | None:
    port = port or cdp_port()
    chrome = chrome_executable()
    if not chrome.is_file():
        logger.error("Chrome not found: %s", chrome)
        return None
    profile = chrome_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        str(chrome),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        IM_WORKSPACE,
    ]
    try:
        proc = subprocess.Popen(  # noqa: S603
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc
    except OSError as exc:
        logger.error("launch chrome: %s", exc)
        return None


def _wait_cdp(port: int, timeout_sec: float = 30.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if _cdp_ready(port):
            return True
        time.sleep(1.0)
    return False


def _close_chrome(proc: subprocess.Popen | None) -> None:
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


async def onboard_async(
    *,
    wait_sec: float = 300.0,
    launch: bool = True,
    close_browser: bool = True,
    warm_inners: bool = True,
    export_pack: bool = True,
    port: int | None = None,
) -> dict[str, Any]:
    """Full CDP onboard: Chrome → scan login → sync → warm 169B → pack."""
    apply_runtime_env()
    port = port or cdp_port()
    report: dict[str, Any] = {"steps": [], "port": port}
    chrome_proc: subprocess.Popen | None = None

    try:
        if not _cdp_ready(port):
            if not launch:
                report["ok"] = False
                report["error"] = f"CDP not ready on port {port}"
                return report
            _set_job(phase="launching")
            chrome_proc = _launch_chrome(port=port)
            if not _wait_cdp(port, timeout_sec=35.0):
                report["ok"] = False
                report["error"] = "Chrome CDP 启动超时"
                return report
        report["steps"].append("cdp_ready")

        from playwright.async_api import async_playwright

        from pigeon_protocol.cdp_bridge import _find_feige_page
        from pigeon_protocol.conv_list_cdp import _wait_for_feige_login
        from pigeon_protocol.session import load_session, save_session

        session = load_session()
        _set_job(phase="waiting_login")

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}",
                timeout=30000,
            )
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = _find_feige_page(ctx.pages)
            if page is None:
                page = await ctx.new_page()

            if "pc_seller_v2/main" not in (page.url or ""):
                await page.goto(IM_WORKSPACE, wait_until="domcontentloaded", timeout=30000)
                report["steps"].append("goto_workspace")

            warm = await page.evaluate(
                """() => ({
                  href: location.href,
                  shopId: (document.cookie.match(/SHOP_ID=(\\d+)/) || [])[1] || "",
                  pigeonCid: (document.cookie.match(/PIGEON_CID=([^;]+)/) || [])[1] || "",
                })"""
            )
            report["warm_before"] = warm

            if not (warm.get("shopId") and warm.get("pigeonCid")):
                warm = await _wait_for_feige_login(page, wait_sec=wait_sec)
                report["warm_after"] = warm
                if not warm.get("logged_in"):
                    report["ok"] = False
                    report["error"] = "扫码超时或未进入工作台（请在浏览器中登录飞鸽）"
                    return report
            report["steps"].append("login_ok")

            _set_job(phase="syncing")
            await page.wait_for_timeout(5000)

            from pigeon_protocol.session_sync import CdpSessionSync

            sync = CdpSessionSync(session, port=port, timeout_sec=30.0)
            report["session_sync"] = await sync._sync_async()
            report["steps"].append("session_sync")

            from pigeon_protocol.feige_init import bootstrap_feige_session, probe_backstage_session

            link = probe_backstage_session(session)
            report["backstage_probe"] = link
            if link.get("ok"):
                report["steps"].append("backstage_ok")
            else:
                report["steps"].append(f"backstage:{link.get('code')}")

            boot = bootstrap_feige_session(session, persist=True)
            report["feige_bootstrap"] = {
                k: boot.get(k) for k in ("ok", "steps", "get_link_info") if k in boot
            }
            report["steps"].append("feige_bootstrap")

        if warm_inners:
            _set_job(phase="warming")
            from pigeon_protocol.cdp_warm_inners import warm_session_inners_async

            warm_report = await warm_session_inners_async(port=port, launch=False)
            report["warm_inners"] = warm_report
            if warm_report.get("ok"):
                report["steps"].append("warm_inners")
            else:
                report["steps"].append("warm_inners_partial")

        save_session(session)

        from pigeon_protocol.session_health import auto_heal_session
        from pigeon_protocol.foundation.pure_prepare import prepare_pure_runtime

        auto_heal_session(session, refresh_csrf=True, refresh_sign=True)
        prepare_pure_runtime(session, probe_ws=False)
        save_session(session)

        from pigeon_protocol.session_portable import (
            assess_runtime_ready,
            export_session_pack,
            sync_portable_inner_sidecar,
        )

        sync_portable_inner_sidecar(session, force=True)
        ready = assess_runtime_ready(session)
        report["readiness"] = ready

        if export_pack and ready.get("send_ready"):
            pack_path = project_root() / "session" / "pigeon_session_pack.zip"
            pack = export_session_pack(pack_path)
            report["session_pack"] = pack
            if pack.get("ok"):
                report["steps"].append("export_pack")

        report["ok"] = bool(ready.get("send_ready"))
        if not report["ok"]:
            report["error"] = "; ".join(ready.get("blockers") or []) or "发信未就绪"
        return report
    finally:
        if close_browser:
            _close_chrome(chrome_proc)


def run_onboard(
    *,
    wait_sec: float = 300.0,
    launch: bool = True,
    close_browser: bool = True,
    warm_inners: bool = True,
    export_pack: bool = True,
    background: bool = False,
) -> dict[str, Any]:
    """Run onboard sync or in background thread."""

    def _worker() -> None:
        _set_job(running=True, phase="starting", error="", blockers=[])
        try:
            report = asyncio.run(
                onboard_async(
                    wait_sec=wait_sec,
                    launch=launch,
                    close_browser=close_browser,
                    warm_inners=warm_inners,
                    export_pack=export_pack,
                )
            )
            ready = report.get("readiness") or {}
            _set_job(
                running=False,
                phase="done" if report.get("ok") else "error",
                error=report.get("error") or "",
                send_ready=bool(ready.get("send_ready")),
                listen_ready=bool(ready.get("listen_ready")),
                blockers=list(ready.get("blockers") or []),
                report=report,
            )
        except Exception as exc:
            logger.exception("cdp onboard")
            _set_job(running=False, phase="error", error=str(exc)[:300])

    if background:
        if _job.get("running"):
            return {"ok": False, "error": "onboard already running", **job_snapshot()}
        threading.Thread(target=_worker, daemon=True, name="cdp-onboard").start()
        return {"ok": True, "started": True, **job_snapshot()}

    _set_job(running=True, phase="starting", error="")
    report = asyncio.run(
        onboard_async(
            wait_sec=wait_sec,
            launch=launch,
            close_browser=close_browser,
            warm_inners=warm_inners,
            export_pack=export_pack,
        )
    )
    ready = report.get("readiness") or {}
    _set_job(
        running=False,
        phase="done" if report.get("ok") else "error",
        error=report.get("error") or "",
        send_ready=bool(ready.get("send_ready")),
        listen_ready=bool(ready.get("listen_ready")),
        blockers=list(ready.get("blockers") or []),
        report=report,
    )
    return report


def start_onboard_background(**kwargs: Any) -> dict[str, Any]:
    kwargs["background"] = True
    return run_onboard(**kwargs)


def write_report(path: Path | None = None) -> Path:
    path = path or (project_root() / "analysis" / "cdp_onboard_report.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    snap = job_snapshot()
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


_warm_lock = threading.Lock()
_warm_job: dict[str, Any] = {"running": False, "phase": "idle", "error": "", "report": {}}


def warm_job_snapshot() -> dict[str, Any]:
    with _warm_lock:
        return dict(_warm_job)


def start_warm_background(*, launch: bool = True) -> dict[str, Any]:
    """Seed 169B inner — Rust SDK (no browser) when CDP disabled, else CDP warm."""

    def _worker() -> None:
        with _warm_lock:
            if _warm_job.get("running"):
                return
            _warm_job.update({"running": True, "phase": "launching", "error": "", "report": {}})
        try:
            apply_runtime_env()
            from pigeon_protocol.pure_config import cdp_allowed

            if not cdp_allowed():
                with _warm_lock:
                    _warm_job["phase"] = "rust_sdk"
                from pigeon_protocol.foundation.rust_sdk_inner import rust_sdk_seed_send_inner
                from pigeon_protocol.session import load_session, save_session
                from pigeon_protocol.session_readiness import assess_runtime_ready

                session = load_session()
                rust = rust_sdk_seed_send_inner(session)
                ready = assess_runtime_ready(session, probe_backstage=False)
                save_session(session)
                ok = bool(rust.get("ingested_classes")) or bool(ready.get("send_ready"))
                with _warm_lock:
                    _warm_job.update(
                        {
                            "running": False,
                            "phase": "done" if ok else "error",
                            "error": "" if ok else (rust.get("error") or "Rust SDK 未生成 169B")[:200],
                            "send_ready": bool(ready.get("send_ready")),
                            "report": {"rust": rust, "readiness": ready, "via": "rust_sdk"},
                        }
                    )
                return

            port = cdp_port()
            chrome_proc = None
            if not _cdp_ready(port):
                if not launch:
                    raise RuntimeError(f"CDP not ready on port {port}")
                with _warm_lock:
                    _warm_job["phase"] = "launching"
                chrome_proc = _launch_chrome(port=port)
                if not _wait_cdp(port, timeout_sec=35.0):
                    raise RuntimeError("Chrome CDP 启动超时")
            with _warm_lock:
                _warm_job["phase"] = "warming"
            from pigeon_protocol.cdp_warm_inners import warm_session_inners_async

            report = asyncio.run(warm_session_inners_async(port=port, launch=False))
            from pigeon_protocol.session import load_session, save_session
            from pigeon_protocol.session_readiness import heal_for_send

            session = load_session()
            heal = heal_for_send(session, save=True)
            ready = heal.get("readiness") or {}
            report["readiness"] = ready
            save_session(session)
            with _warm_lock:
                _warm_job.update(
                    {
                        "running": False,
                        "phase": "done" if report.get("ok") else "error",
                        "error": "" if report.get("ok") else "warm 未完成",
                        "send_ready": bool(ready.get("send_ready")),
                        "report": report,
                    }
                )
            if chrome_proc:
                _close_chrome(chrome_proc)
        except Exception as exc:
            logger.exception("cdp warm")
            with _warm_lock:
                _warm_job.update({"running": False, "phase": "error", "error": str(exc)[:300]})

    with _warm_lock:
        if _warm_job.get("running"):
            return {"ok": False, "error": "warm already running", **warm_job_snapshot()}
    threading.Thread(target=_worker, daemon=True, name="cdp-warm").start()
    return {"ok": True, "started": True, **warm_job_snapshot()}

