#!/usr/bin/env python3
"""Download and install 抖店/飞鸽 desktop client to E:\\feige-electron."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INSTALL_DIR = Path("E:/feige-electron")
DOWNLOAD_PAGE = "https://im.jinritemai.com/download"
FEIGE_PID = "7005992474254514440"
TRON_CHECK_API = "https://tron.jiyunhudong.com/api/sdk/check_update"


def resolve_official_installer_url() -> dict:
    """Resolve latest win x64 installer from ByteDance tron update API."""
    import urllib.parse

    q = urllib.parse.urlencode(
        {"pid": FEIGE_PID, "uid": "", "branch": "master", "buildId": ""}
    )
    url = f"{TRON_CHECK_API}?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read())
    manifest = (((payload.get("data") or {}).get("manifest") or {}).get("win32") or {})
    extra = manifest.get("extra") or {}
    dl = extra.get("downloadUrlX64") or extra.get("downloadUrl") or manifest.get("pkgUrl") or ""
    return {
        "ok": bool(dl),
        "download_url": dl,
        "version": manifest.get("version", ""),
        "build_id": (payload.get("data") or {}).get("buildId"),
    }


def fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def probe_download_url_playwright() -> dict:
    import asyncio

    from playwright.async_api import async_playwright

    async def _run() -> dict:
        report: dict = {"captured": [], "download_url": "", "filename": ""}
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            def on_req(req) -> None:
                u = req.url
                if any(k in u.lower() for k in (".exe", "download", "setup", "package", "tos", "client")):
                    report["captured"].append(u)

            page.on("request", on_req)
            await page.goto(DOWNLOAD_PAGE, wait_until="networkidle", timeout=90000)
            btn = page.get_by_text("下载Win桌面客户端").first
            if not await btn.count():
                report["error"] = "win download button not found"
                await browser.close()
                return report

            try:
                async with page.expect_download(timeout=180000) as dl_info:
                    await btn.click()
                dl = await dl_info.value
                report["download_url"] = dl.url
                report["filename"] = dl.suggested_filename
                dest = ROOT / "downloads" / (dl.suggested_filename or "DouyinShop_Setup.exe")
                dest.parent.mkdir(parents=True, exist_ok=True)
                await dl.save_as(str(dest))
                report["saved"] = str(dest)
            except Exception as exc:
                report["error"] = str(exc)
                # fallback: parse captured requests
                for u in reversed(report["captured"]):
                    if ".exe" in u.lower() or "setup" in u.lower():
                        report["download_url"] = u
                        break

            await browser.close()
        return report

    return asyncio.run(_run())


def download_file(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    print(f"Downloading {url} -> {dest}", flush=True)
    with urllib.request.urlopen(req, timeout=600) as r, dest.open("wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"  {done * 100 // total}% ({done // (1024*1024)} MB)", flush=True)
    return dest


def find_installer(download_dir: Path) -> Path | None:
    exes = sorted(download_dir.glob("*.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in exes:
        if p.stat().st_size > 50_000_000:
            return p
    return exes[0] if exes else None


def silent_install(installer: Path, target: Path) -> dict:
    target.mkdir(parents=True, exist_ok=True)
    log = target / "install.log"
    # NSIS / electron-builder common flags
    cmd_variants = [
        [str(installer), "/S", f"/D={target}"],
        [str(installer), "/S", f"/D={str(target).replace('/', chr(92))}"],
        [str(installer), "/VERYSILENT", f"/DIR={target}"],
    ]
    last_err = ""
    for cmd in cmd_variants:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            log.write_text(
                f"cmd={' '.join(cmd)}\nrc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}\n",
                encoding="utf-8",
            )
            if proc.returncode == 0:
                return {"ok": True, "cmd": cmd, "log": str(log)}
            last_err = proc.stderr or proc.stdout or f"rc={proc.returncode}"
        except Exception as exc:
            last_err = str(exc)
    return {"ok": False, "error": last_err, "log": str(log)}


def scan_installed(root: Path) -> list[dict]:
    needles = (
        b"packedMessage",
        b"PigeonIMCreateMessage",
        b"webviewBridge",
        b"invokeWithoutReturn",
    )
    matches: list[dict] = []
    if not root.is_dir():
        return matches
    for p in root.rglob("*"):
        if p.suffix.lower() not in {".exe", ".dll", ".node", ".asar"}:
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if len(data) < 1024:
            continue
        hits = [n.decode("ascii", errors="ignore") for n in needles if n in data]
        if hits:
            matches.append({"path": str(p), "size": len(data), "hits": hits})
    return matches[:30]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install-dir", default=str(DEFAULT_INSTALL_DIR))
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--installer", default="", help="Use existing installer path")
    args = ap.parse_args()

    install_dir = Path(args.install_dir)
    download_dir = ROOT / "downloads"
    report: dict = {"install_dir": str(install_dir)}

    installer: Path | None = Path(args.installer) if args.installer else None
    if installer and not installer.is_file():
        print(json.dumps({"error": f"installer not found: {installer}"}, ensure_ascii=False))
        return 2

    if not installer:
        meta = resolve_official_installer_url()
        report["official"] = meta
        if not meta.get("ok"):
            pw = probe_download_url_playwright()
            report["playwright"] = pw
            saved = pw.get("saved")
            if saved and Path(saved).is_file():
                installer = Path(saved)
            elif pw.get("download_url"):
                name = pw.get("filename") or "DouyinShop_Setup.exe"
                installer = download_dir / name
                report["download"] = str(download_file(pw["download_url"], installer))
            else:
                print(json.dumps({"ok": False, "report": report}, ensure_ascii=False, indent=2))
                return 1
        else:
            name = Path(meta["download_url"]).name or "doudian_v1.1.7_x64.exe"
            installer = download_dir / name
            if not installer.is_file() or installer.stat().st_size < 50_000_000:
                report["download"] = str(download_file(meta["download_url"], installer))

    report["installer"] = str(installer)
    report["installer_size"] = installer.stat().st_size if installer.is_file() else 0

    if args.download_only:
        print(json.dumps({"ok": True, "report": report}, ensure_ascii=False, indent=2))
        return 0

    inst = silent_install(installer, install_dir)
    report["install"] = inst

    # Also check common per-user install locations
    scan_roots = [
        install_dir,
        Path("E:/feige-electron"),
        Path.home() / "AppData/Local/Programs/feige",
        Path.home() / "AppData/Local/feige",
        Path.home() / "AppData/Local/抖店",
    ]
    all_matches: list[dict] = []
    for r in scan_roots:
        all_matches.extend(scan_installed(r))
    report["packedMessage_scan"] = all_matches
    report["ok"] = inst.get("ok", False) or bool(all_matches)

    out = ROOT / "analysis" / "feige_install_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
