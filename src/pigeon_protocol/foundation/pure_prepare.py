"""One-shot pure-protocol runtime preparation — no CDP/Node/HAR at call time."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("pigeon.pure_prepare")


def sync_standalone_bundle(session, *, force: bool = False) -> dict[str, Any]:
    """
    Export session-scoped WS inner canonical + init mapping to standalone_bundle/.
    Safe to call after bootstrap or first successful send.
    """
    from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache, inner_class_registry
    from pigeon_protocol.foundation.ws_session_inner import _session_key

    report: dict[str, Any] = {"exported": []}
    cached = _load_session_class_cache(session)
    need = {ic.class_id for ic in inner_class_registry().values()}
    have = {k for k in cached if not k.startswith("_") and k != "__init_sync__"}
    if not force and len(have & need) < 4:
        report["skipped"] = f"only {len(have)} send classes cached"
        return report

    try:
        from pathlib import Path
        import json
        import sys

        root = Path(__file__).resolve().parents[3]
        script = root / "scripts" / "export_ws_inner_bundle.py"
        if script.is_file():
            from pigeon_protocol.subprocess_util import run_hidden

            r = run_hidden(
                [sys.executable, str(script)],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if r.returncode == 0:
                report["exported"].append("ws_inner_canonical.json")
            else:
                report["export_error"] = (r.stderr or r.stdout or "")[:300]
    except Exception as exc:
        report["export_error"] = str(exc)

    try:
        from pigeon_protocol.foundation.init_inner_mapper import export_init_mapping

        path = export_init_mapping(session, raw=None)
        report["exported"].append(path.name)
    except Exception as exc:
        logger.debug("init mapping export: %s", exc)

    report["session_key"] = _session_key(session)
    report["cached_send_classes"] = len(have & need)
    return report


def prepare_pure_runtime(session, *, probe_ws: bool = False) -> dict[str, Any]:
    """
    Full pure-protocol warm-up chain (HTTP only):
    0. portable restore — 169B inners from sidecar/bundle (new machine)
    1. auto_heal — CSRF, tokens, feige bootstrap
    2. ensure_ws_ready — WS URL + inner bootstrap
    3. sync standalone_bundle exports
    4. foundation health snapshot
    """
    from pigeon_protocol.foundation.status import foundation_report
    from pigeon_protocol.session_health import auto_heal_session, ensure_ws_ready

    from pigeon_protocol.foundation.pigeon_sdk_delegate import ensure_send_inner
    from pigeon_protocol.pure_config import pure_only_mode

    report: dict[str, Any] = {"steps": []}
    pure = pure_only_mode()

    try:
        from pigeon_protocol.session_portable import ensure_portable_ready, sync_portable_inner_sidecar

        portable = ensure_portable_ready(session, heal=False)
        report["portable"] = portable
        if portable.get("steps"):
            report["steps"].append(f"portable:{','.join(portable['steps'][:4])}")
    except Exception as exc:
        logger.debug("portable restore skipped: %s", exc)

    health = auto_heal_session(session, refresh_csrf=True, refresh_sign=True)
    report["session_health"] = health.to_dict()
    if health.fixes_applied:
        report["steps"].append(f"auto_heal:{','.join(health.fixes_applied[:5])}")

    try:
        from pigeon_protocol.conv_list import warm_conv_session

        conv_warm = warm_conv_session(session)
        report["conv_warm"] = conv_warm
        if conv_warm.get("steps"):
            report["steps"].append(f"conv_warm:{','.join(conv_warm['steps'][:4])}")
    except Exception as exc:
        logger.debug("conv warm skipped: %s", exc)

    ws = ensure_ws_ready(session, probe=probe_ws)
    report["ws_ready"] = ws
    if ws.get("ws_ready"):
        report["steps"].append("ws_ready")
    inners = ws.get("ws_inners") or {}
    if inners.get("ready"):
        report["steps"].append(f"inners:{inners.get('cached_classes', 0)}")

    try:
        inner_seed = ensure_send_inner(session, cdp_if_available=not pure)
        report["inner_seed"] = inner_seed
        if inner_seed.get("ok"):
            report["steps"].append(f"inner_seed:{inner_seed.get('via', 'ok')}")
    except Exception as exc:
        logger.debug("inner seed skipped: %s", exc)

    try:
        from pigeon_protocol.foundation.ws_inner_health import ensure_fresh_session_inners

        inner_refresh = ensure_fresh_session_inners(session, cdp_if_needed=not pure)
        report["inner_refresh"] = inner_refresh
        if inner_refresh.get("refreshed"):
            report["steps"].append(inner_refresh.get("via") or "inner_refresh")
    except Exception as exc:
        logger.debug("inner refresh skipped: %s", exc)

    try:
        from pigeon_protocol.cdp_bridge import cdp_ready

        if cdp_ready():
            if not pure:
                try:
                    from pigeon_protocol.foundation.cdp_session_light import sync_from_feige_page

                    cdp_sync = sync_from_feige_page(session)
                    report["cdp_ws_sync"] = cdp_sync
                    if cdp_sync.get("ok"):
                        report["steps"].append("cdp_ws_sync")
                except Exception as exc:
                    logger.debug("cdp ws sync: %s", exc)
            try:
                from pigeon_protocol.conv_sign_snapshot import refresh_snapshots_from_cdp

                snap = refresh_snapshots_from_cdp(session)
                report["conv_snapshot_bootstrap"] = snap
                if snap.get("saved"):
                    report["steps"].append(f"conv_snapshot:{','.join(snap['saved'][:3])}")
            except Exception as exc:
                logger.debug("conv snapshot bootstrap: %s", exc)
            try:
                from pigeon_protocol.foundation.ws_frontier_sign import bootstrap_frontier_cache_from_cdp

                fr = bootstrap_frontier_cache_from_cdp(session)
                report["frontier_bootstrap"] = fr
                if fr.get("cached"):
                    report["steps"].append(f"frontier_cache:{fr['cached']}")
            except Exception as exc:
                logger.debug("frontier bootstrap: %s", exc)
            try:
                from pigeon_protocol.ws_sign_bucket import gap_harvest_plan
                from pigeon_protocol.ws_template_harvest import bootstrap_templates_sync, missing_lengths, QUICK_LADDER

                plan = gap_harvest_plan()
                priority = (plan.get("harvest_priority") or missing_lengths(QUICK_LADDER))[:6]
                if priority:
                    wh = bootstrap_templates_sync(lengths=priority)
                    report["ws_gap_harvest"] = wh
                    if wh.get("harvested"):
                        report["steps"].append(f"ws_harvest:{wh['harvested']}")
            except Exception as exc:
                logger.debug("ws gap harvest: %s", exc)
    except Exception as exc:
        logger.debug("cdp bootstrap skipped: %s", exc)

    bundle = sync_standalone_bundle(session)
    report["bundle_sync"] = bundle
    if bundle.get("exported"):
        report["steps"].append(f"bundle:{','.join(bundle['exported'])}")

    try:
        from pigeon_protocol.session_portable import sync_portable_inner_sidecar

        sidecar = sync_portable_inner_sidecar(session)
        report["portable_sidecar"] = sidecar
        if sidecar.get("written"):
            report["steps"].append("portable_sidecar")
    except Exception as exc:
        logger.debug("portable sidecar sync: %s", exc)

    from pigeon_protocol.session import save_session

    try:
        save_session(session)
    except Exception as exc:
        logger.debug("save_session after prepare: %s", exc)

    foundation = foundation_report(session)
    report["foundation"] = foundation.to_dict()
    inner_h = (report.get("inner_refresh") or {}).get("health_after") or {}
    if not inner_h:
        try:
            from pigeon_protocol.foundation.ws_inner_health import session_inner_health

            inner_h = session_inner_health(session)
            report["inner_health"] = inner_h
        except Exception:
            inner_h = {}
    send_ready = bool(inner_h.get("ready") or inner_h.get("full"))
    report["ok"] = foundation.ok and bool(ws.get("ws_ready")) and send_ready
    from pigeon_protocol.pure_config import BUNDLE_CONV_SNAPSHOT

    report["pure_ready"] = {
        "listen": bool(session.ws_urls),
        "send": send_ready,
        "context": bool(session.cookies),
        "orders": foundation.http_sign.get("python_abogus") and foundation.relay_headers,
        "inner_unified": inner_h.get("unified"),
        "conv_snapshot": BUNDLE_CONV_SNAPSHOT.is_file(),
    }
    return report
