"""CDP warm session-scoped 169B inners via UI send."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

from pigeon_protocol.runtime_paths import cdp_port, project_root

logger = logging.getLogger("pigeon.cdp_warm")

CLASS_CANONICAL: list[tuple[str, int]] = [
    ("E", 1),
    ("A", 6),
    ("B", 9),
    ("C", 77),
    ("D", 78),
    ("F", 82),
    ("G", 64),
]


def _inner_from_b64(b64: str) -> dict[str, Any]:
    from pigeon_protocol.ws_sign import locate_signature_region
    from pigeon_protocol.ws_sign_decode import decode_blob, guess_inner_layout

    raw = base64.b64decode(b64)
    region = locate_signature_region(raw)
    if not region:
        return {"error": "no_sig_region", "frame_len": len(raw)}
    inner = decode_blob(region.blob)
    return {
        "frame_len": len(raw),
        "inner_hex": inner.hex(),
        "layout": guess_inner_layout(inner),
    }


async def _warm_class(page, text: str, class_name: str, text_b: int) -> dict[str, Any]:
    from pigeon_protocol.ws_template_harvest import (
        INSTALL_CAPTURE_JS,
        POLL_SAMPLES_JS,
        _ensure_chat_open,
        _send_via_ui,
        sample_to_event,
    )

    await _ensure_chat_open(page)
    await page.evaluate(INSTALL_CAPTURE_JS)
    before = await page.evaluate(POLL_SAMPLES_JS)
    before_n = len(before) if isinstance(before, list) else 0

    send = await _send_via_ui(page, text)
    row: dict[str, Any] = {"class": class_name, "text_b": text_b, "send": send, "ok": False}

    for _ in range(30):
        await asyncio.sleep(0.4)
        samples = await page.evaluate(POLL_SAMPLES_JS)
        if not isinstance(samples, list) or len(samples) <= before_n:
            continue
        for raw in samples[before_n:]:
            event = sample_to_event(raw)
            if event.get("text_byte_length") == text_b:
                inner_info = _inner_from_b64(raw.get("b64") or "")
                row.update(
                    {
                        "ok": True,
                        "text_byte_length": text_b,
                        "frame_len": event.get("frame_length"),
                        **inner_info,
                    }
                )
                return row
    row["error"] = "no_ws_sample"
    return row


async def warm_session_inners_async(
    *,
    port: int | None = None,
    launch: bool = False,
) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready
    from pigeon_protocol.cdp_launch import ensure_cdp_ready
    from pigeon_protocol.foundation.ws_blob_compute import _store_session_class_inner, inner_class_for_text_b
    from pigeon_protocol.session import load_session, save_session
    from pigeon_protocol.ws_template_harvest import (
        _ensure_chat_open,
        _ensure_ws_connected,
        text_for_byte_length,
    )

    port = port or cdp_port()
    if launch:
        if not ensure_cdp_ready(launch=True, wait_sec=30.0):
            return {"ok": False, "error": f"CDP not ready on {port}"}
    elif not cdp_ready(port):
        return {"ok": False, "error": f"CDP not ready on {port}"}

    session = load_session()
    report: dict[str, Any] = {"classes": [], "stored": []}

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = next(
            (pg for pg in ctx.pages if "jinritemai" in (pg.url or "")),
            ctx.pages[0] if ctx.pages else await ctx.new_page(),
        )
        report["page"] = (page.url or "")[:200]
        report["chat"] = await _ensure_chat_open(page)
        report["ws"] = await _ensure_ws_connected(page)
        if report["ws"].get("state") != 1:
            return report

        for class_name, text_b in CLASS_CANONICAL:
            text = text_for_byte_length(text_b)
            row = await _warm_class(page, text, class_name, text_b)
            report["classes"].append(row)
            if row.get("ok") and row.get("inner_hex"):
                ic = inner_class_for_text_b(text_b)
                if ic:
                    inner = bytes.fromhex(row["inner_hex"])
                    _store_session_class_inner(session, ic.class_id, inner)
                    report["stored"].append(
                        {
                            "class": class_name,
                            "class_id": ic.class_id[:16],
                            "header_hex": inner[:8].hex(),
                            "text_b": text_b,
                        }
                    )
            await asyncio.sleep(1.0)

    save_session(session)

    try:
        from pigeon_protocol.foundation.cdp_session_light import sync_from_feige_page_async

        report["cdp_session"] = await sync_from_feige_page_async(session)
        save_session(session)
    except Exception as exc:
        report["cdp_session"] = {"error": str(exc)[:200]}

    live_hex = [r.get("inner_hex") for r in report["classes"] if r.get("inner_hex")]
    if live_hex and len(set(live_hex)) == 1:
        from pigeon_protocol.foundation.ws_blob_compute import inner_class_registry

        inner = bytes.fromhex(live_hex[0])
        for ic in inner_class_registry().values():
            _store_session_class_inner(session, ic.class_id, inner)
        report["unified_inner"] = {
            "header_hex": inner[:8].hex(),
            "sha256_prefix": live_hex[0][:32],
            "class_keys": len(inner_class_registry()),
        }
        save_session(session)

    try:
        from pigeon_protocol.foundation.ws_inner_normalize import normalize_session_inners

        report["normalize"] = normalize_session_inners(session, persist=True)
    except Exception as exc:
        report["normalize"] = {"error": str(exc)[:120]}

    try:
        from pigeon_protocol.foundation.pure_prepare import sync_standalone_bundle

        report["bundle_sync"] = sync_standalone_bundle(session)
    except Exception as exc:
        report["bundle_sync"] = {"error": str(exc)[:200]}

    report["ok"] = len(report["stored"]) >= 4
    out_path = project_root() / "analysis" / "cdp_session_inners.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


# Alias for cdp_onboard module
onboard_warm_inners = warm_session_inners_async

_last_warm_attempt: float = 0.0
_min_warm_interval_sec = 300.0


def auto_warm_inners_if_needed(
    *,
    launch: bool = True,
    background: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """
    Backstage 已就绪但 169B 缺失/陈旧时，自动 CDP warm（可启动 Chrome）。
    bootstrap 用同步；keepalive 用 background 避免阻塞。
    """
    import asyncio
    import time

    from pigeon_protocol.foundation.ws_inner_health import session_inner_health
    from pigeon_protocol.session import load_session, save_session

    global _last_warm_attempt

    session = load_session()
    inner = session_inner_health(session)
    if not inner.get("needs_cdp_warm") and not force:
        return {"ok": True, "skipped": True, "reason": "inners_ok", "inner": inner}

    from pigeon_protocol.pure_config import cdp_allowed

    if not cdp_allowed():
        try:
            from pigeon_protocol.foundation.rust_sdk_inner import rust_sdk_seed_send_inner

            rust = rust_sdk_seed_send_inner(session)
            inner2 = session_inner_health(session)
            return {
                "ok": bool(rust.get("ingested_classes")) or inner2.get("ready"),
                "skipped": not bool(rust.get("ingested_classes")),
                "reason": "rust_sdk_no_cdp",
                "rust": rust,
                "inner": inner,
                "inner_after": inner2,
            }
        except Exception as exc:
            return {"ok": False, "skipped": True, "reason": "rust_sdk_failed", "error": str(exc)[:200], "inner": inner}

    from pigeon_protocol.feige_init import probe_backstage_session

    backstage = probe_backstage_session(session)
    if not backstage.get("ok"):
        return {
            "ok": False,
            "skipped": True,
            "reason": "backstage_not_ok",
            "inner": inner,
            "backstage": {k: backstage.get(k) for k in ("ok", "code", "expired")},
        }

    now = time.time()
    if not force and _last_warm_attempt and now - _last_warm_attempt < _min_warm_interval_sec:
        return {
            "ok": False,
            "skipped": True,
            "reason": "cooldown",
            "inner": inner,
            "retry_after_sec": int(_min_warm_interval_sec - (now - _last_warm_attempt)),
        }

    _last_warm_attempt = now

    if background:
        from pigeon_protocol.cdp_onboard import start_warm_background

        job = start_warm_background(launch=launch)
        return {
            "ok": bool(job.get("started") or job.get("running")),
            "background": True,
            "warm_job": job,
            "inner": inner,
        }

    from pigeon_protocol.cdp_launch import ensure_cdp_ready

    port = cdp_port()
    if launch and not ensure_cdp_ready(launch=True, wait_sec=35.0):
        return {"ok": False, "error": "CDP 启动失败", "inner": inner}

    report = asyncio.run(warm_session_inners_async(port=port, launch=False))
    inner_after = session_inner_health(session)
    report["inner_before"] = inner
    report["inner_after"] = inner_after
    report["ok"] = bool(report.get("ok")) or bool(inner_after.get("full"))
    try:
        save_session(session)
    except OSError as exc:
        report["save_error"] = str(exc)[:120]
    return report

