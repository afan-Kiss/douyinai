"""WS Frontier frame signing — byted_acrawler.frontierSign({ X-MS-STUB: md5(payload) })."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger("pigeon.ws_frontier_sign")

# im_proto.IMCMD values that trigger frontierSign (from IM SDK RE)
SIGN_CMDS = frozenset(
    {
        100,  # SEND_MESSAGE (typical)
        609,  # CREATE_CONVERSATION_V2
        610,
        611,
        612,
        613,
        614,
        615,
        616,
        617,
        618,
        619,
    }
)


def x_ms_stub(payload: bytes) -> str:
    """MD5 hex digest of serialized protobuf payload (IM SDK frontierSign input)."""
    return hashlib.md5(payload).hexdigest()


def frontier_sign_stub_input(payload: bytes) -> dict[str, str]:
    return {"X-MS-STUB": x_ms_stub(payload)}


def needs_frontier_sign(cmd: int) -> bool:
    return cmd in SIGN_CMDS


def template_send_bypass() -> bool:
    """
    ComputedBlobStrategy sends harvested 3127B templates with patched inner only.
    Frontier headers are already embedded — no live acrawler required on this path.
    """
    return True


def _load_session_cache(session: Any) -> dict[str, dict[str, str]]:
    if session is None:
        return {}
    extra = getattr(session, "extra", None) or {}
    cache = extra.get("frontier_sign_cache")
    return cache if isinstance(cache, dict) else {}


def _save_session_cache(session: Any, cache: dict[str, dict[str, str]]) -> None:
    if session is None:
        return
    if not hasattr(session, "extra") or session.extra is None:
        session.extra = {}
    session.extra["frontier_sign_cache"] = cache


def frontier_sign_headers(payload: bytes, *, session: Any = None) -> dict[str, str]:
    """
    Compute WS frame signature headers.

    IM SDK: byted_acrawler.frontierSign({ X-MS-STUB: md5(payload) })
    Returns extra headers to merge into im_proto.Frame (e.g. X-Bogus, X-Gnarly).
    """
    stub_in = frontier_sign_stub_input(payload)
    stub_key = stub_in["X-MS-STUB"]

    cache = _load_session_cache(session)
    if stub_key in cache:
        return dict(cache[stub_key])

    # CDP / browser runtime
    try:
        from pigeon_protocol.cdp_bridge import cdp_ready

        if cdp_ready():
            out = _frontier_sign_cdp(stub_in)
            if out:
                cache[stub_key] = out
                _save_session_cache(session, cache)
                return out
    except Exception as exc:
        logger.debug("frontier_sign cdp skipped: %s", exc)

    # Node jsdom if acrawler loaded in analysis page
    try:
        out = _frontier_sign_node(stub_in)
        if out:
            cache[stub_key] = out
            _save_session_cache(session, cache)
            return out
    except Exception as exc:
        logger.debug("frontier_sign node skipped: %s", exc)

    return {}


def bootstrap_frontier_cache_from_cdp(session: Any = None) -> dict[str, Any]:
    """Pre-cache frontierSign stubs from live Feige page (CDP) for offline reuse."""
    report: dict[str, Any] = {"cached": 0, "stubs": []}
    try:
        from pigeon_protocol.cdp_bridge import cdp_ready

        if not cdp_ready():
            report["skipped"] = "cdp not ready"
            return report
    except Exception as exc:
        report["skipped"] = str(exc)
        return report

    probes = (
        b"\x08\x01",
        b"\x08d\x10\x01",
        b"",
    )
    for payload in probes:
        hdr = frontier_sign_headers(payload, session=session)
        if hdr:
            report["cached"] += 1
            report["stubs"].append(x_ms_stub(payload))
    report["ok"] = report["cached"] > 0
    return report


def frontier_sign_status(session: Any = None) -> dict[str, Any]:
    """Report frontier sign capability for foundation status."""
    cache = _load_session_cache(session)
    node_ok = False
    cdp_ok = False
    try:
        from pigeon_protocol.cdp_bridge import cdp_ready

        cdp_ok = cdp_ready()
    except Exception:
        pass
    try:
        node_ok = bool(_frontier_sign_node({"X-MS-STUB": "d41d8cd98f00b204e9800998ecf8427e"}))
    except Exception:
        pass
    return {
        "template_bypass": template_send_bypass(),
        "cached_stubs": len(cache),
        "cdp_ready": cdp_ok,
        "node_acrawler": node_ok,
        "pure_send_path": "template_embedded_headers",
    }


def _frontier_sign_cdp(stub_in: dict[str, str]) -> dict[str, str]:
    import asyncio

    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready

    if not cdp_ready():
        return {}

    async def _run() -> dict[str, str]:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            page = next(
                (pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")),
                browser.contexts[0].pages[0],
            )
            result = await page.evaluate(
                """(stubIn) => {
                  if (!window.byted_acrawler?.frontierSign) return null;
                  try {
                    return window.byted_acrawler.frontierSign(stubIn);
                  } catch (e) { return { error: String(e) }; }
                }""",
                stub_in,
            )
            if isinstance(result, dict) and not result.get("error"):
                return {str(k): str(v) for k, v in result.items()}
            return {}

    try:
        return asyncio.run(_run())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()


def _frontier_sign_node(stub_in: dict[str, str]) -> dict[str, str]:
    import subprocess
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parents[3] / "scripts"
    for name in ("run_frontier_glue.mjs", "run_frontier_sign.mjs"):
        script = scripts_dir / name
        if not script.is_file():
            continue
        proc = subprocess.run(
            ["node", str(script)],
            input=json.dumps(stub_in),
            capture_output=True,
            text=True,
            timeout=25,
            cwd=str(script.parent.parent),
        )
        if proc.returncode != 0 and not proc.stdout.strip():
            continue
        try:
            doc = json.loads(proc.stdout)
            headers = {str(k): str(v) for k, v in (doc.get("headers") or {}).items()}
            if headers:
                return headers
        except json.JSONDecodeError:
            continue
    return {}
