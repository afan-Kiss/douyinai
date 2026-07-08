"""Portable session pack — copy login + WS send inners to another machine (no browser)."""
from __future__ import annotations

import json
import logging
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("pigeon.session_portable")

ROOT = Path(__file__).resolve().parents[2]
SESSION_DIR = ROOT / "session"
SESSION_FILE = SESSION_DIR / "session.json"
INNER_CACHE = SESSION_DIR / "ws_inner_cache.json"
PORTABLE_INNER = SESSION_DIR / "ws_inner_portable.json"
BUNDLE_DIR = ROOT / "standalone_bundle"
ANALYSIS_ENV = ROOT / "analysis" / "bdms_browser_env.json"

PACK_VERSION = 1


def refresh_paths() -> None:
    """Reload account-scoped paths after switch."""
    global SESSION_DIR, SESSION_FILE, INNER_CACHE, PORTABLE_INNER, BUNDLE_DIR
    from pigeon_protocol.account_context import (
        bundle_dir,
        inner_cache_file,
        portable_inner_file,
        session_dir,
        session_file,
    )

    SESSION_DIR = session_dir()
    SESSION_FILE = session_file()
    INNER_CACHE = inner_cache_file()
    PORTABLE_INNER = portable_inner_file()
    BUNDLE_DIR = bundle_dir()


def _pack_files() -> tuple[str, ...]:
    from pigeon_protocol.account_context import pack_rel_files

    return pack_rel_files()


def _session_key(session) -> str:
    from pigeon_protocol.foundation.ws_session_inner import _session_key

    return _session_key(session)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def sync_portable_inner_sidecar(session, *, force: bool = False) -> dict[str, Any]:
    """
    Mirror ws_inner_canonical → session/ws_inner_portable.json so copying session/
    alone carries 169B inners for the same login.
    """
    from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache, inner_class_registry
    from pigeon_protocol.foundation.ws_inner_bootstrap import load_bundle_canonical

    report: dict[str, Any] = {"written": False}
    src = BUNDLE_DIR / "ws_inner_canonical.json"
    cached = _load_session_class_cache(session)
    need = {ic.class_id for ic in inner_class_registry().values()}
    have = {k for k in cached if k in need}

    if not force and len(have) < 4 and not src.is_file():
        report["skipped"] = f"only {len(have)} send classes cached"
        return report

    payload: dict[str, Any] | None = _read_json(src)
    if payload and payload.get("session_key") != _session_key(session):
        payload = None

    if payload is None and len(have) >= 4:
        from pigeon_protocol.foundation.ws_blob_compute import classify_inner

        reg = inner_class_registry()
        classes: list[dict[str, Any]] = []
        for ic in reg.values():
            inner = cached.get(ic.class_id)
            if not inner:
                continue
            classes.append(
                {
                    "class_id": ic.class_id,
                    "name": ic.name,
                    "header_hex": inner[:8].hex(),
                    "canonical_text_b": ic.canonical_text_b,
                    "text_lengths": list(ic.text_lengths),
                    "inner_hex": inner.hex(),
                    "layout": classify_inner(inner),
                    "source": "session_cache",
                }
            )
        distinct = {c["inner_hex"] for c in classes}
        payload = {
            "version": 1,
            "session_key": _session_key(session),
            "formula": "portable sidecar — travels with session.json",
            "unified_inner": len(distinct) == 1,
            "class_count": len(classes),
            "classes": classes,
            "exported_at": int(time.time()),
        }
    if not payload or not payload.get("classes"):
        report["skipped"] = "no inner classes to export"
        return report

    try:
        from pigeon_protocol.foundation.ws_inner_edbx import edbx_meta_from_inner, is_edbx_inner, portable_edbx_meta

        edbx = portable_edbx_meta(session)
        if not edbx.get("trailer_hex"):
            for row in payload.get("classes") or []:
                hx = str((row or {}).get("inner_hex") or "")
                if not hx:
                    continue
                try:
                    inner = bytes.fromhex(hx)
                except ValueError:
                    continue
                if is_edbx_inner(inner):
                    edbx = edbx_meta_from_inner(inner)
                    break
        if edbx.get("trailer_hex"):
            payload["edbx"] = edbx
    except Exception as exc:
        logger.debug("portable edbx meta: %s", exc)

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    PORTABLE_INNER.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report["written"] = True
    report["classes"] = payload.get("class_count", len(payload.get("classes") or []))
    report["path"] = str(PORTABLE_INNER)
    return report


def _ingest_inner_doc(session, doc: dict[str, Any], *, source: str, trust_pack: bool = False) -> list[str]:
    from pigeon_protocol.foundation.ws_blob_compute import _store_session_class_inner
    from pigeon_protocol.foundation.ws_inner_edbx import is_edbx_inner, store_envelope_template

    bound = str(doc.get("session_key") or "")
    if bound and bound != _session_key(session) and not trust_pack:
        logger.debug("skip inner doc from %s — session_key mismatch", source)
        return []

    applied: list[str] = []
    seen_edbx = False
    for row in doc.get("classes") or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("class_id") or "")
        hx = str(row.get("inner_hex") or "")
        if not cid or not hx:
            continue
        try:
            inner = bytes.fromhex(hx)
        except ValueError:
            continue
        if len(inner) != 169:
            continue
        _store_session_class_inner(session, cid, inner)
        applied.append(cid)
        if is_edbx_inner(inner) and not seen_edbx:
            store_envelope_template(session, inner, source=source)
            seen_edbx = True

    edbx_doc = doc.get("edbx")
    if isinstance(edbx_doc, dict) and edbx_doc.get("trailer_hex"):
        extra = getattr(session, "extra", None) or {}
        if not hasattr(session, "extra"):
            session.extra = extra
        extra["edbx_trailer_hex"] = str(edbx_doc["trailer_hex"])
        if edbx_doc.get("prefix_sample_hex"):
            extra["edbx_prefix_hex"] = str(edbx_doc["prefix_sample_hex"])
        if edbx_doc.get("route_sample"):
            extra["edbx_route_sample"] = str(edbx_doc["route_sample"])
        if edbx_doc.get("field12_us"):
            extra["edbx_field12_us"] = int(edbx_doc["field12_us"])

    if applied:
        logger.info("restored %s inner classes from %s", len(applied), source)
    return applied


def restore_portable_inners(session, *, trust_pack: bool = False) -> dict[str, Any]:
    """Load 169B send inners from sidecar / bundle when ws_inner_cache is empty on a new machine."""
    from pigeon_protocol.foundation.ws_inner_health import session_inner_health

    report: dict[str, Any] = {"sources": []}
    health = session_inner_health(session)
    if health.get("full"):
        report["ok"] = True
        report["via"] = "cache"
        report["health"] = health
        return report

    for label, path in (
        ("portable_sidecar", PORTABLE_INNER),
        ("bundle_canonical", BUNDLE_DIR / "ws_inner_canonical.json"),
    ):
        doc = _read_json(path)
        if not doc:
            continue
        applied = _ingest_inner_doc(session, doc, source=label, trust_pack=trust_pack)
        if applied:
            report["sources"].append(f"{label}:{len(applied)}")

    try:
        from pigeon_protocol.foundation.ws_inner_normalize import normalize_session_inners

        normalize_session_inners(session, persist=True)
    except Exception as exc:
        logger.debug("normalize after portable restore: %s", exc)

    health = session_inner_health(session)
    report["health"] = health
    report["ok"] = bool(health.get("ready"))
    report["via"] = "portable_restore" if report.get("sources") else "missing"
    return report


def ensure_portable_ready(session, *, heal: bool = True, trust_pack: bool = False) -> dict[str, Any]:
    """
    New-machine bootstrap (HTTP + Node only):
    1. restore 169B inners from portable sidecar / bundle
    2. optional token + CSRF auto-heal (machine-independent)
    """
    report: dict[str, Any] = {"steps": []}
    inner = restore_portable_inners(session, trust_pack=trust_pack)
    report["inner_restore"] = inner
    if inner.get("sources"):
        report["steps"].append(f"inners:{','.join(inner['sources'])}")

    if heal:
        from pigeon_protocol.session_health import auto_heal_session

        health = auto_heal_session(session, refresh_csrf=True, refresh_sign=True)
        report["session_health"] = health.to_dict()
        if health.fixes_applied:
            report["steps"].append(f"heal:{','.join(health.fixes_applied[:6])}")

    sidecar = sync_portable_inner_sidecar(session)
    report["sidecar"] = sidecar
    if sidecar.get("written"):
        report["steps"].append("sidecar_sync")

    report["ok"] = bool(inner.get("ok")) and bool(session.cookies)
    report["send_ready"] = bool((inner.get("health") or {}).get("ready"))

    if not report["send_ready"]:
        import os

        allow_node = os.environ.get("PIGEON_NO_RUST", "").strip().lower() not in ("1", "true", "yes")
        try:
            from pigeon_protocol.foundation.im_access_token import resolve_im_access_token
            from pigeon_protocol.foundation.ws_inner_edbx import derive_edbx_inner_session, ingest_derived_inners
            from pigeon_protocol.foundation.ws_inner_health import session_inner_health

            if allow_node:
                resolve_im_access_token(session, allow_node=True)
            derived, edbx = derive_edbx_inner_session(session)
            if derived:
                ingest_derived_inners(session, derived, source="portable_derive")
                report["edbx_derive"] = edbx
                report["steps"].append("edbx_derive")
                health = session_inner_health(session)
                report["send_ready"] = bool(health.get("ready"))
        except Exception as exc:
            report["edbx_derive_error"] = str(exc)[:120]

    return report


def export_session_pack(dest: Path, *, include_analysis_env: bool = True) -> dict[str, Any]:
    """Export one zip/folder with everything needed to run on another PC."""
    from pigeon_protocol.session import load_session, save_session

    dest = Path(dest)
    session = load_session()
    try:
        save_session(session)
    except OSError:
        pass

    sync_portable_inner_sidecar(session, force=True)
    try:
        from pigeon_protocol.foundation.pure_prepare import sync_standalone_bundle

        sync_standalone_bundle(session, force=True)
    except Exception as exc:
        logger.debug("bundle sync on export: %s", exc)

    if include_analysis_env and ANALYSIS_ENV.is_file():
        BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ANALYSIS_ENV, BUNDLE_DIR / "bdms_browser_env.json")

    manifest = {
        "version": PACK_VERSION,
        "session_key": _session_key(session),
        "exported_at": int(time.time()),
        "shop_id": str(session.shop_id or session.cookies.get("SHOP_ID") or ""),
        "files": [],
    }

    if dest.suffix.lower() == ".zip":
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for rel in _pack_files():
                if rel.startswith("bundle/"):
                    path = BUNDLE_DIR / rel.removeprefix("bundle/")
                else:
                    path = SESSION_DIR / rel
                if path.is_file():
                    zf.write(path, rel)
                    manifest["files"].append(rel)
        out_path = dest
    else:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for rel in _pack_files():
            if rel.startswith("bundle/"):
                path = BUNDLE_DIR / rel.removeprefix("bundle/")
            else:
                path = SESSION_DIR / rel
            if path.is_file():
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                manifest["files"].append(rel)
        out_path = dest

    return {
        "ok": bool(manifest["files"]),
        "path": str(out_path),
        "files": manifest["files"],
        "session_key": manifest["session_key"],
        "hint": "Copy zip to new PC → python run.py import-session-pack --file <path>",
    }


def import_session_pack(src: Path, *, run_prepare: bool = True, set_active: bool = False) -> dict[str, Any]:
    """Import portable pack and warm pure-protocol runtime (no browser)."""
    from pigeon_protocol.session import load_session, save_session

    src = Path(src)
    report: dict[str, Any] = {"copied": []}

    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src, "r") as zf:
            manifest_raw = zf.read("manifest.json") if "manifest.json" in zf.namelist() else b"{}"
            try:
                report["manifest"] = json.loads(manifest_raw.decode("utf-8"))
            except json.JSONDecodeError:
                report["manifest"] = {}
            for name in zf.namelist():
                if name.endswith("/") or name == "manifest.json":
                    continue
                from pigeon_protocol.account_context import resolve_import_target

                try:
                    target = resolve_import_target(name)
                except ValueError as exc:
                    report.setdefault("skipped", []).append(f"{name}:{exc}")
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))
                report["copied"].append(name)
    elif src.is_dir():
        manifest = _read_json(src / "manifest.json") or {}
        report["manifest"] = manifest
        from pigeon_protocol.account_context import legacy_pack_rel_files, resolve_import_target

        candidates = list(_pack_files())
        candidates.extend(legacy_pack_rel_files())
        seen: set[str] = set()
        for rel in candidates:
            if rel in seen:
                continue
            seen.add(rel)
            path = src / rel
            if path.is_file():
                target = resolve_import_target(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                report["copied"].append(rel)
    else:
        return {"ok": False, "error": f"unsupported pack: {src}"}

    session = load_session()
    from pigeon_protocol.session_startup import rebind_pack_inners_to_session

    rebind_pack_inners_to_session(session)
    portable = ensure_portable_ready(session, heal=True, trust_pack=True)
    report["portable"] = portable

    if run_prepare:
        try:
            from pigeon_protocol.foundation.pure_prepare import prepare_pure_runtime

            prep = prepare_pure_runtime(session, probe_ws=False)
            report["prepare"] = prep
            report["ready"] = bool(prep.get("ok"))
        except Exception as exc:
            report["prepare_error"] = str(exc)[:200]
            report["ready"] = bool(portable.get("send_ready"))
    else:
        report["ready"] = bool(portable.get("send_ready"))

    try:
        save_session(session)
    except OSError as exc:
        report["save_error"] = str(exc)[:120]

    try:
        from pigeon_protocol.account_context import register_account_from_session

        report["account_id"] = register_account_from_session(session, set_active=set_active)
    except Exception as exc:
        report["account_register_error"] = str(exc)[:120]

    from pigeon_protocol.session_readiness import assess_runtime_ready as assess_ext

    ready = assess_ext(session)
    report["readiness"] = ready
    report["send_ready"] = ready.get("send_ready")
    report["listen_ready"] = ready.get("listen_ready")
    report["conv_ready"] = ready.get("conv_ready")
    report["blockers"] = list(ready.get("blockers") or [])
    report["recommended_action"] = ready.get("recommended_action")
    report["needs_cdp_onboard"] = ready.get("needs_cdp_onboard")
    report["ok"] = bool(ready.get("full_ready") or ready.get("ok"))
    return report


def assess_runtime_ready(session) -> dict[str, Any]:
    """Delegate to session_readiness (backstage probe + recommended_action)."""
    from pigeon_protocol.session_readiness import assess_runtime_ready as assess_ext

    return assess_ext(session)


def _assess_runtime_ready_legacy(session) -> dict[str, Any]:
    """Legacy base assess — used only inside session_readiness."""
    from pigeon_protocol.foundation.bdms_sign import sign_available
    from pigeon_protocol.foundation.status import foundation_report
    from pigeon_protocol.foundation.ws_inner_health import session_inner_health

    inner = session_inner_health(session)
    foundation = foundation_report(session)

    send_ready = bool(inner.get("ready"))
    listen_ready = bool(session.ws_urls)
    conv_ready = bool(sign_available()) and bool(foundation.relay_headers)

    blockers: list[str] = []
    if not send_ready:
        blockers.append("发信未就绪：169B inner 缺失（将自动通过 Rust SDK 合成，无需浏览器）")
    if not listen_ready:
        blockers.append("WS 监听未就绪：无可用 ws_url")
    if not conv_ready:
        blockers.append("会话列表未就绪：签名或 relay 请求头缺失")

    return {
        "send_ready": send_ready,
        "listen_ready": listen_ready,
        "conv_ready": conv_ready,
        "inner_full": bool(inner.get("full")),
        "inner_unified": bool(inner.get("unified")),
        "inner_cached": inner.get("cached_count", 0),
        "blockers": blockers,
        "ok": listen_ready and conv_ready,
        "full_ready": send_ready and listen_ready and conv_ready,
    }


def post_login_bootstrap(
    session,
    *,
    qr_client: Any = None,
    qr_state: Any = None,
    export_pack: bool = True,
    skip_fxg_complete: bool = False,
) -> dict[str, Any]:
    """
    QR 登录确认后的完整纯协议预热：
    feige bootstrap → auto_heal → prepare-pure → rust_sdk 补 inner → 导出 sidecar/zip
    """
    from pigeon_protocol.qr_login import QR_CONFIRMED
    from pigeon_protocol.session import save_session

    report: dict[str, Any] = {"steps": []}

    if qr_state is not None and qr_client is not None:
        if str(getattr(qr_state, "status", "") or "") == QR_CONFIRMED and not skip_fxg_complete:
            try:
                qr_state.cookies = qr_client.complete_fxg_login(qr_state)
                qr_state.cookies.update(qr_client.open_feige_workspace())
            except Exception as exc:
                report["fxg_complete_error"] = str(exc)[:200]
        qr_client.apply_to_session(session, qr_state)
        report["steps"].append("apply_session")
        try:
            qr_client.seed_from_session(session)
            qr_state.cookies = qr_client.open_feige_workspace()
            qr_client.apply_to_session(session, qr_state, replace_auth=False)
            report["steps"].append("open_feige_workspace")
        except Exception as exc:
            report["feige_workspace_error"] = str(exc)[:200]

    try:
        from pigeon_protocol.feige_init import bootstrap_feige_session, probe_backstage_session

        backstage = probe_backstage_session(session)
        report["backstage_probe"] = backstage
        if backstage.get("ok"):
            report["steps"].append("backstage:ok")
        elif backstage.get("expired") or not backstage.get("ok"):
            try:
                from pigeon_protocol.session_renewal import establish_im_session_http

                renew = establish_im_session_http(session, persist=False)
                report["im_renew"] = renew
                if renew.get("ok"):
                    report["steps"].append("im_renew:ok")
                    backstage = probe_backstage_session(session)
                    report["backstage_probe"] = backstage
            except Exception as exc:
                report["im_renew_error"] = str(exc)[:200]
            if not backstage.get("ok"):
                report["backstage_blocker"] = (
                    "pigeon backstage 未就绪：已尝试 HTTP 续期，仍建议浏览器登录飞鸽"
                )

        boot = bootstrap_feige_session(session, persist=True)
        report["feige_bootstrap"] = {k: boot.get(k) for k in ("ok", "steps", "get_message_by_init") if k in boot}
        report["steps"].append("feige_bootstrap")

        try:
            import os

            from pigeon_protocol.foundation.im_access_token import resolve_im_access_token
            from pigeon_protocol.foundation.ws_inner_edbx import derive_edbx_inner_session, ingest_derived_inners

            allow_node = os.environ.get("PIGEON_NO_RUST", "").strip().lower() not in ("1", "true", "yes")
            token, token_via = resolve_im_access_token(session, allow_node=allow_node)
            report["im_access_token"] = {"via": token_via, "preview": token[:12] + "..." if token else None}

            inner, edbx = derive_edbx_inner_session(session)
            report["edbx_derive"] = edbx
            if inner:
                classes = ingest_derived_inners(session, inner, source="post_login_derive")
                report["edbx_derive"]["ingested_classes"] = classes
                report["steps"].append("edbx_derive:ok")
                sidecar = sync_portable_inner_sidecar(session, force=True)
                report["edbx_sidecar"] = sidecar
        except Exception as exc:
            report["edbx_derive_error"] = str(exc)[:200]
    except Exception as exc:
        report["feige_bootstrap_error"] = str(exc)[:200]

    from pigeon_protocol.session_health import auto_heal_session

    health = auto_heal_session(session, refresh_csrf=True, refresh_sign=True)
    report["session_health"] = health.to_dict()
    if health.fixes_applied:
        report["steps"].append(f"heal:{','.join(health.fixes_applied[:5])}")

    try:
        from pigeon_protocol.foundation.pure_prepare import prepare_pure_runtime

        prep = prepare_pure_runtime(session, probe_ws=False)
        report["prepare"] = {
            "ok": prep.get("ok"),
            "steps": prep.get("steps"),
            "pure_ready": prep.get("pure_ready"),
        }
        report["steps"].append("prepare_pure")
    except Exception as exc:
        report["prepare_error"] = str(exc)[:200]

    ready = assess_runtime_ready(session)
    import os

    no_rust = os.environ.get("PIGEON_NO_RUST", "").strip().lower() in ("1", "true", "yes")
    no_cdp = os.environ.get("PIGEON_NO_CDP", "").strip().lower() in ("1", "true", "yes")
    edbx_ok = bool((report.get("edbx_derive") or {}).get("ok"))

    if not ready.get("send_ready") and not no_rust:
        try:
            from pigeon_protocol.foundation.rust_sdk_inner import rust_sdk_seed_send_inner

            rust = rust_sdk_seed_send_inner(session)
            report["rust_sdk"] = {
                "ok": rust.get("ok"),
                "via": rust.get("via"),
                "error": rust.get("error"),
                "ingested": rust.get("ingested_classes"),
            }
            if rust.get("ingested_classes"):
                report["steps"].append("rust_sdk_inner")
            from pigeon_protocol.session_readiness import assess_runtime_ready as assess_ext

            ready = assess_ext(session)
        except Exception as exc:
            report["rust_sdk_error"] = str(exc)[:200]
    elif edbx_ok and ready.get("send_ready"):
        report["rust_sdk"] = {"skipped": True, "reason": "edbx_derive_ok"}

    if not ready.get("send_ready") and not no_cdp:
        try:
            from pigeon_protocol.cdp_warm_inners import auto_warm_inners_if_needed

            warm = auto_warm_inners_if_needed(launch=False, background=False, force=True)
            report["inner_warm"] = {
                "ok": warm.get("ok"),
                "reason": warm.get("reason"),
                "skipped": warm.get("skipped"),
            }
            if warm.get("ok") and not warm.get("skipped"):
                report["steps"].append("inner_warm")
            from pigeon_protocol.session_readiness import assess_runtime_ready as assess_ext2

            ready = assess_ext2(session)
        except Exception as exc:
            report["inner_warm_error"] = str(exc)[:200]

    sidecar = sync_portable_inner_sidecar(session, force=True)
    report["sidecar"] = sidecar
    if sidecar.get("written"):
        report["steps"].append("portable_sidecar")

    pack_path = SESSION_DIR / "pigeon_session_pack.zip"
    if export_pack:
        pack = export_session_pack(pack_path)
        report["session_pack"] = {
            "ok": pack.get("ok"),
            "path": pack.get("path"),
            "files": len(pack.get("files") or []),
        }
        if pack.get("ok"):
            report["steps"].append("export_session_pack")
        if not ready.get("send_ready"):
            report["session_pack"]["note"] = "已导出 Cookie/WS；发信 inner 将在后台 Rust SDK 自动补全"

    from pigeon_protocol.session_readiness import assess_runtime_ready as assess_ext

    ready = assess_ext(session)
    report["readiness"] = ready
    report["send_ready"] = ready.get("send_ready")
    report["listen_ready"] = ready.get("listen_ready")
    report["conv_ready"] = ready.get("conv_ready")
    report["blockers"] = list(ready.get("blockers") or [])
    if report.get("backstage_blocker") and report["backstage_blocker"] not in report["blockers"]:
        report["blockers"].insert(0, report["backstage_blocker"])
    report["recommended_action"] = ready.get("recommended_action")
    report["needs_cdp_onboard"] = ready.get("needs_cdp_onboard")
    report["ok"] = bool(ready.get("send_ready") or ready.get("listen_ready"))

    try:
        save_session(session)
    except OSError as exc:
        report["save_error"] = str(exc)[:120]

    try:
        from pigeon_protocol.account_context import register_account_from_session

        report["account_id"] = register_account_from_session(session, set_active=True)
    except Exception as exc:
        report["account_register_error"] = str(exc)[:120]

    return report
