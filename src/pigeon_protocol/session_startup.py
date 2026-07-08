"""Startup bootstrap — auto-import pack, restore inners, heal tokens (cross-machine)."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from pigeon_protocol.session_portable import import_session_pack, sync_portable_inner_sidecar

logger = logging.getLogger("pigeon.session_startup")


def _default_pack_path() -> Path:
    from pigeon_protocol.account_context import session_pack_file

    return session_pack_file()


_BOOTSTRAP_DONE = False


def session_needs_import(session) -> bool:
    from pigeon_protocol.account_context import session_file

    cookies = session.cookies or {}
    sf = session_file()
    if not sf.is_file():
        return True
    if not (cookies.get("sessionid") or cookies.get("sid_tt")):
        return True
    if not cookies.get("SHOP_ID"):
        return True
    return False


def rebind_pack_inners_to_session(session) -> dict[str, Any]:
    """After pack import, align inner sidecar session_key with current cookies."""
    from pigeon_protocol.session_portable import (
        BUNDLE_DIR,
        INNER_CACHE,
        PORTABLE_INNER,
        _read_json,
        _session_key,
    )

    sk = _session_key(session)
    report: dict[str, Any] = {"session_key": sk, "updated": []}
    for label, path in (
        ("portable", PORTABLE_INNER),
        ("bundle", BUNDLE_DIR / "ws_inner_canonical.json"),
        ("cache", INNER_CACHE),
    ):
        doc = _read_json(path)
        if not doc:
            continue
        if doc.get("session_key") == sk:
            continue
        doc["session_key"] = sk
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(doc, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            report["updated"].append(label)
        except OSError as exc:
            report.setdefault("errors", []).append(f"{label}:{exc}")
    return report


def bootstrap_on_startup(
    *,
    auto_import_pack: bool = True,
    pack_path: Path | None = None,
    export_if_ready: bool = True,
) -> dict[str, Any]:
    """
    Idempotent startup:
    1. auto-import session pack if session weak / missing
    2. rebind + restore 169B inners
    3. heal tokens / WS / CSRF
    4. optional refresh export pack when healthy
    """
    global _BOOTSTRAP_DONE
    from pigeon_protocol.session import load_session, save_session
    from pigeon_protocol.session_readiness import assess_runtime_ready, heal_for_send

    report: dict[str, Any] = {"steps": []}
    pack = Path(pack_path) if pack_path else _default_pack_path()
    session = load_session()
    if auto_import_pack and session_needs_import(session) and pack.is_file():
        logger.info("auto-import session pack: %s", pack)
        imp = import_session_pack(pack, run_prepare=True)
        report["auto_import"] = imp
        report["steps"].append("auto_import_pack")
        session = load_session()

    rebind = rebind_pack_inners_to_session(session)
    if rebind.get("updated"):
        report["rebind_inners"] = rebind
        report["steps"].append("rebind_inners")

    heal = heal_for_send(session, save=True)
    report["heal"] = {
        "ok": heal.get("ok"),
        "send_ready": heal.get("send_ready"),
        "steps": heal.get("steps"),
    }
    report["steps"].extend(heal.get("steps") or [])

    ready = assess_runtime_ready(session, probe_backstage=True)
    if session.cookies.get("sessionid") and not ready.get("backstage_ok"):
        try:
            from pigeon_protocol.session_renewal import renew_session_if_needed

            renew = renew_session_if_needed(session, persist=True)
            report["renew"] = renew
            if renew.get("steps"):
                report["steps"].extend([f"renew:{s}" for s in renew["steps"][:6]])
            ready = assess_runtime_ready(session, probe_backstage=True)
        except Exception as exc:
            report["renew_error"] = str(exc)[:200]

    ready = assess_runtime_ready(session, probe_backstage=False)
    if ready.get("backstage_ok") and not ready.get("send_ready"):
        try:
            from pigeon_protocol.cdp_warm_inners import auto_warm_inners_if_needed

            warm = auto_warm_inners_if_needed(launch=True, background=False)
            report["inner_warm"] = {
                "ok": warm.get("ok"),
                "skipped": warm.get("skipped"),
                "reason": warm.get("reason"),
            }
            if warm.get("ok") and not warm.get("skipped"):
                report["steps"].append("inner_warm")
            ready = assess_runtime_ready(session, probe_backstage=False)
        except Exception as exc:
            report["inner_warm_error"] = str(exc)[:200]

    sidecar = sync_portable_inner_sidecar(session, force=True)
    if sidecar.get("written"):
        report["steps"].append("sidecar_sync")

    ready = assess_runtime_ready(session, probe_backstage=True)
    report["readiness"] = ready
    report["send_ready"] = ready.get("send_ready")
    report["listen_ready"] = ready.get("listen_ready")
    report["recommended_action"] = ready.get("recommended_action")

    if export_if_ready and ready.get("send_ready"):
        pack_age = _pack_age_sec(pack)
        if not pack.is_file() or pack_age > 86400:
            try:
                from pigeon_protocol.session_portable import export_session_pack

                exp = export_session_pack(pack)
                report["export_pack"] = {"ok": exp.get("ok"), "path": exp.get("path")}
                if exp.get("ok"):
                    report["steps"].append("export_pack")
            except Exception as exc:
                report["export_error"] = str(exc)[:200]

    try:
        save_session(session)
    except OSError as exc:
        report["save_error"] = str(exc)[:120]

    report["ok"] = bool(ready.get("send_ready") or ready.get("listen_ready"))
    _BOOTSTRAP_DONE = True
    return report


def _pack_age_sec(path: Path) -> float:
    if not path.is_file():
        return 1e9
    return max(0.0, time.time() - path.stat().st_mtime)


def bootstrap_done() -> bool:
    return _BOOTSTRAP_DONE
