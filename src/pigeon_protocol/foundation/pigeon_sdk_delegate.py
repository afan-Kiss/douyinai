"""Pigeon Rust SDK delegate — cmd 11327 PigeonIMCreateMessage inner extraction."""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

logger = logging.getLogger("pigeon.sdk_delegate")

CMD_CREATE_MESSAGE = 11327

HOOK_INVOKE_JS = r"""
() => {
  if (window.__pigeonInvokeHook?.installed) return { already: true };
  const cap = { installed: true, calls: [], inners: [] };
  window.__pigeonInvokeHook = cap;

  const tryHook = (bridge, label) => {
    if (!bridge || bridge.__pigeonHooked) return false;
    const wrap = (name) => {
      const orig = bridge[name];
      if (typeof orig !== "function") return;
      bridge[name] = function(...args) {
        const row = { t: Date.now(), fn: name, label, args_preview: args?.slice?.(0, 4) };
        try {
          const out = orig.apply(this, args);
          if (out && typeof out.then === "function") {
            return out.then((res) => {
              row.result_type = typeof res;
              try { row.result_json = JSON.stringify(res).slice(0, 4000); } catch (e) {}
              cap.calls.push(row);
              return res;
            });
          }
          row.result_type = typeof out;
          cap.calls.push(row);
          return out;
        } catch (e) {
          row.error = String(e);
          cap.calls.push(row);
          throw e;
        }
      };
    };
    for (const fn of ["invokeWithoutReturn", "invokeAsync", "invoke"]) wrap(fn);
    bridge.__pigeonHooked = true;
    return true;
  };

  cap.hooked = tryHook(window.webviewBridge, "webviewBridge");
  cap._timer = setInterval(() => tryHook(window.webviewBridge, "webviewBridge"), 500);
  return { installed: true, hooked: cap.hooked, hasBridge: !!window.webviewBridge };
}
"""

CREATE_MESSAGE_PROBE_JS = r"""
async () => {
  const bridge = window.webviewBridge;
  if (!bridge?.getSDKClient) return { ok: false, error: "no webviewBridge.getSDKClient" };
  try {
    const client = await bridge.getSDKClient();
    if (!client?.createMessage) return { ok: false, error: "no createMessage on SDK client" };
    return { ok: true, client_keys: Object.keys(client).slice(0, 24) };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}
"""


def _decode_inner_from_b64(b64: str) -> bytes | None:
    from pigeon_protocol.ws_sign import locate_signature_region
    from pigeon_protocol.ws_sign_decode import decode_blob

    try:
        raw = base64.b64decode(b64)
        region = locate_signature_region(raw)
        if not region:
            return None
        inner = decode_blob(region.blob)
        return inner if len(inner) == 169 else None
    except Exception:
        return None


async def cdp_hook_invoke_async() -> dict[str, Any]:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready

    if not cdp_ready():
        return {"ok": False, "error": "cdp not ready"}

    report: dict[str, Any] = {}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = next(
            (pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")),
            browser.contexts[0].pages[0],
        )
        report["page"] = page.url[:160]
        report["hook"] = await page.evaluate(HOOK_INVOKE_JS)
        report["sdk_probe"] = await page.evaluate(CREATE_MESSAGE_PROBE_JS)
        hook = await page.evaluate("() => window.__pigeonInvokeHook || {}")
        report["calls"] = (hook.get("calls") or [])[-20:]
    report["ok"] = bool(report.get("hook", {}).get("hooked") or report.get("sdk_probe", {}).get("ok"))
    return report


def cdp_seed_send_inner(session, *, text: str = "好") -> dict[str, Any]:
    """
    Seed 169B send inner via CDP UI send (PigeonIMCreateMessage side effect).
    Falls back to existing ws_cdp_inner_ingest refresh.
    """
    from pigeon_protocol.foundation.ws_cdp_inner_ingest import refresh_inners_via_cdp

    refresh = refresh_inners_via_cdp(session, warm_all=False)
    if refresh.get("ok") and refresh.get("applied"):
        try:
            from pigeon_protocol.foundation.ws_inner_normalize import normalize_session_inners

            normalize_session_inners(session, persist=True)
        except Exception as exc:
            refresh["normalize_error"] = str(exc)[:120]
    return refresh


def http_bootstrap_send_inner(session) -> dict[str, Any]:
    """
    HTTP-only inner bootstrap (no CDP):
    1. bundle canonical export
    2. get_message_by_init ingest (INIT_SYNC only — documents gap)
    3. promote unified inner if all class keys already agree
    """
    from pigeon_protocol.foundation.ws_inner_bootstrap import bootstrap_session_inners
    from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache, inner_class_registry

    report = bootstrap_session_inners(session, scan_init=True)
    cached = _load_session_class_cache(session)
    inners = [cached.get(ic.class_id) for ic in inner_class_registry().values() if cached.get(ic.class_id)]
    if inners and len({x.hex() for x in inners}) == 1:
        report["unified_inner"] = inners[0][:8].hex()
        report["http_send_ready"] = True
    else:
        report["http_send_ready"] = len(cached) >= 4
    report["ok"] = report.get("http_send_ready", False)
    return report


def ensure_send_inner(session, *, cdp_if_available: bool = True) -> dict[str, Any]:
    """Best-effort send inner seed: cache → HTTP bootstrap → Rust SDK → optional CDP."""
    from pigeon_protocol.foundation.ws_inner_health import session_inner_health
    from pigeon_protocol.pure_config import cdp_allowed, pure_only_mode

    pure = pure_only_mode()
    use_cdp = cdp_if_available and cdp_allowed() and not pure

    health = session_inner_health(session)
    if health.get("full") and not health.get("stale_pool"):
        return {"ok": True, "via": "cache", "health": health}
    if health.get("stale_pool"):
        try:
            from pigeon_protocol.foundation.ws_inner_normalize import normalize_session_inners

            normalize_session_inners(session, persist=True)
            health = session_inner_health(session)
            if health.get("full") and not health.get("stale_pool"):
                return {"ok": True, "via": "normalize", "health": health}
        except Exception as exc:
            logger.debug("normalize on stale cache: %s", exc)

    http = http_bootstrap_send_inner(session)
    health = session_inner_health(session)
    if health.get("full") and not health.get("stale_pool"):
        return {"ok": True, "via": "http_bootstrap", "http": http, "health": health}

    if pure or not use_cdp:
        try:
            from pigeon_protocol.foundation.rust_sdk_inner import rust_sdk_seed_send_inner

            rust = rust_sdk_seed_send_inner(session)
            health = session_inner_health(session)
            if rust.get("ingested_classes") or (health.get("full") and not health.get("stale_pool")):
                return {
                    "ok": health.get("ready", False),
                    "via": "rust_sdk",
                    "http": http,
                    "rust": rust,
                    "health": health,
                }
        except Exception as exc:
            logger.debug("rust sdk seed inner: %s", exc)

    if use_cdp:
        try:
            from pigeon_protocol.cdp_bridge import cdp_ready

            if cdp_ready():
                cdp = cdp_seed_send_inner(session)
                try:
                    from pigeon_protocol.foundation.ws_inner_normalize import normalize_session_inners

                    normalize_session_inners(session, persist=True)
                except Exception as exc:
                    cdp["normalize_error"] = str(exc)[:120]
                health = session_inner_health(session)
                return {
                    "ok": bool(cdp.get("ok")) and health.get("ready"),
                    "via": "cdp_seed",
                    "http": http,
                    "cdp": cdp,
                    "health": health,
                }
        except Exception as exc:
            logger.debug("cdp seed inner: %s", exc)

    try:
        from pigeon_protocol.foundation.ws_inner_normalize import normalize_session_inners

        normalize_session_inners(session, persist=True)
    except Exception as exc:
        logger.debug("normalize skipped: %s", exc)

    return {"ok": health.get("ready", False), "via": "partial", "http": http, "health": health}


def cdp_hook_invoke() -> dict[str, Any]:
    try:
        return asyncio.run(cdp_hook_invoke_async())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(cdp_hook_invoke_async())
        finally:
            loop.close()
