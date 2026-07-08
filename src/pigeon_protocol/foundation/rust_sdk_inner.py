"""Pure-protocol Pigeon Rust SDK invoke — cmd 11327 offline 169B inner (no browser/client)."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("pigeon.rust_sdk_inner")

ROOT = Path(__file__).resolve().parents[3]
INVOKE_SCRIPT = ROOT / "scripts" / "feige_invoke_create_message.mjs"
ROUTE_RE = re.compile(r"(AQ[Cc][A-Za-z0-9_-]{30,120}:\d+::\d+:\d+:pigeon)")


def _build_route(security_user_id: str, shop_id: str) -> str:
    from pigeon_protocol.parsers.pigeon_frame_parser import build_conversation_route

    return build_conversation_route(security_user_id, shop_id)


def _scan_routes_binary(data: bytes) -> list[str]:
    found: list[str] = []
    for m in ROUTE_RE.finditer(data.decode("latin-1", errors="ignore")):
        s = m.group(1)
        if s not in found:
            found.append(s)
    return found


def resolve_conversation_id(session) -> tuple[str, str]:
    """HTTP-only conversation route resolution."""
    env = os.environ.get("PIGEON_CONVERSATION_ID", "").strip()
    if env:
        return env, "env"

    uid_env = os.environ.get("PIGEON_SECURITY_USER_ID", "").strip()
    shop = str(session.shop_id or session.cookies.get("SHOP_ID") or "")
    if uid_env.startswith("AQ"):
        route = _build_route(uid_env, shop)
        if route:
            return route, "env_security_user_id"

    shop = str(session.shop_id or session.cookies.get("SHOP_ID") or "")

    try:
        from pigeon_protocol.conv_list import list_conversations_relay, parse_conversation_items

        raw = list_conversations_relay(session, size=20)
        items = parse_conversation_items(raw)
        for it in items:
            uid = str(it.get("security_user_id") or "")
            if uid.startswith("AQ"):
                route = _build_route(uid, shop)
                if route:
                    return route, "conv_list_relay"
    except Exception as exc:
        logger.debug("conv_list relay: %s", exc)

    # Live HTTP init scan (prefer fresh conversation route)
    try:
        from pigeon_protocol.feige_init import _post_get_message_by_init

        init = _post_get_message_by_init(session)
        if init.get("body_len", 0) > 500:
            from pigeon_protocol.pure_config import STANDALONE_BUNDLE

            resp_path = STANDALONE_BUNDLE / "get_message_by_init_response.bin"
            if resp_path.is_file():
                for route in _scan_routes_binary(resp_path.read_bytes())[:5]:
                    if shop and shop in route:
                        return route, "init_http_response"
    except Exception as exc:
        logger.debug("init route scan: %s", exc)

    # Session bundle export (HTTP bootstrap artifact, not live HAR)
    try:
        import json
        from pigeon_protocol.pure_config import STANDALONE_BUNDLE

        snap = STANDALONE_BUNDLE / "order_sign_snapshot.json"
        if snap.is_file():
            uid = str(json.loads(snap.read_text(encoding="utf-8")).get("sample_body", {}).get("security_user_id") or "")
            if uid.startswith("AQ"):
                route = _build_route(uid, shop)
                if route:
                    return route, "bundle_order_snapshot"
    except Exception as exc:
        logger.debug("bundle order snapshot: %s", exc)

    # Init response / workspace HTML (same HTTP bootstrap, no client)
    try:
        from pigeon_protocol.pure_config import BUNDLE_INIT_BODY, STANDALONE_BUNDLE

        for label, path in (
            ("init_body_bundle", BUNDLE_INIT_BODY),
            ("init_response_bundle", STANDALONE_BUNDLE / "get_message_by_init_response.bin"),
        ):
            if path.is_file():
                for route in _scan_routes_binary(path.read_bytes())[:5]:
                    if shop and shop in route:
                        return route, label
    except Exception:
        pass

    return "", "missing"


def sync_session_ws_for_sdk(session) -> dict[str, Any]:
    """Align query_tokens.token + ws_urls with HTTP bootstrap (web IM constants)."""
    from pigeon_protocol.feige_init import _fetch_get_link_info, bootstrap_feige_session
    from pigeon_protocol.session import save_session
    from pigeon_protocol.ws_url_builder import canonicalize_ws_session, pick_live_ws_url

    link = _fetch_get_link_info(session)
    boot = bootstrap_feige_session(session, persist=False) if not link.get("ok") else {"ok": link.get("ok"), "steps": ["get_link_info"]}
    canon = canonicalize_ws_session(session)
    url = pick_live_ws_url(session)
    try:
        save_session(session)
    except Exception as exc:
        logger.debug("save_session ws sync: %s", exc)

    tok = str(session.query_tokens.get("token") or "")
    if url and not tok:
        tok = (parse_qs(urlparse(url).query).get("token") or [""])[0]
        if tok:
            session.query_tokens["token"] = tok

    return {
        "bootstrap_ok": boot.get("ok"),
        "link_info": link,
        "steps": (boot.get("steps") or [])[-8:],
        "ws_canonical": canon,
        "ws_url": (url or "")[:160],
        "has_token": bool(tok),
        "has_sign": bool(session.query_tokens.get("pigeon_sign")),
        "token_preview": tok[:8] + "..." if tok else None,
    }


def _ingest_inner_hex(session, inner_hex: str, *, source: str = "rust_sdk") -> list[str]:
    from pigeon_protocol.foundation.ws_inner_validate import parse_inner_hex, is_valid_inner_bytes

    try:
        inner = bytes.fromhex(inner_hex)
    except ValueError:
        return []
    if len(inner) != 169:
        return []
    if not is_valid_inner_bytes(inner) and inner[:4] != b"edbX":
        return []

    try:
        from pigeon_protocol.foundation.ws_inner_edbx import is_edbx_inner, store_envelope_template

        if is_edbx_inner(inner):
            store_envelope_template(session, inner, source=source)
            extra = getattr(session, "extra", None) or {}
            steps = {}
            try:
                import json
                from pathlib import Path

                invoke_path = Path(__file__).resolve().parents[3] / "analysis" / "feige_rust_invoke_latest.json"
                if invoke_path.is_file():
                    steps = json.loads(invoke_path.read_text(encoding="utf-8")).get("steps") or {}
            except Exception:
                pass
            cu = steps.get("createUser") or {}
            token = cu.get("access_token_full") or cu.get("access_token")
            if token and isinstance(token, str) and "..." not in token:
                if getattr(session, "extra", None) is None:
                    session.extra = {}
                session.extra["im_access_token"] = token
    except Exception as exc:
        logger.debug("edbX envelope store: %s", exc)

    from pigeon_protocol.foundation.ws_blob_compute import (
        _store_session_class_inner,
        inner_class_registry,
    )

    applied: list[str] = []
    for ic in inner_class_registry().values():
        _store_session_class_inner(session, ic.class_id, inner)
        applied.append(ic.name)
    try:
        from pigeon_protocol.foundation.ws_inner_normalize import normalize_session_inners

        normalize_session_inners(session, persist=True)
    except Exception as exc:
        logger.debug("normalize after rust ingest: %s", exc)
    return applied


def invoke_create_message(
    session,
    *,
    conversation_id: str = "",
    text: str = "好",
    timeout_sec: int = 120,
) -> dict[str, Any]:
    from pigeon_protocol.foundation.rust_sdk_paths import rust_sdk_layout
    from pigeon_protocol.session import save_session

    layout = rust_sdk_layout()
    report: dict[str, Any] = {"layout": layout}

    if not layout.get("ok"):
        report["ok"] = False
        report["error"] = "rust-sdk bundle missing — run: python run.py feige-export-sdk"
        return report

    ws_sync = sync_session_ws_for_sdk(session)
    report["ws_sync"] = ws_sync
    if (ws_sync.get("link_info") or {}).get("error") and "登录" in str((ws_sync.get("link_info") or {}).get("error")):
        report["session_expired"] = True
        report["hint"] = "session cookies expired — run qr-login before rust_sdk seed"

    conv_id = conversation_id or resolve_conversation_id(session)[0]
    report["conversation_id"] = conv_id or None
    if not conv_id:
        report["ok"] = False
        report["error"] = "no conversation_id — HTTP conv_list empty; refresh session cookies"
        return report

    try:
        from pigeon_protocol.foundation.rust_sdk_conv_meta import resolve_conv_sdk_meta

        conv_meta = resolve_conv_sdk_meta(session, conversation_id=conv_id)
        report["conv_meta"] = {k: conv_meta[k] for k in conv_meta if not str(k).startswith("_")}
    except Exception as exc:
        conv_meta = {}
        report["conv_meta_error"] = str(exc)[:120]

    sec_uid = ""
    if conv_id.startswith("AQ"):
        sec_uid = conv_id.split(":", 1)[0]

    session_path = __import__(
        "pigeon_protocol.account_context", fromlist=["session_file"]
    ).session_file()
    try:
        save_session(session)
    except Exception:
        pass

    env = os.environ.copy()
    env["PIGEON_CONVERSATION_ID"] = conv_id
    if conv_meta.get("_short_id_full"):
        env["PIGEON_CONV_SHORT_ID"] = str(conv_meta["_short_id_full"])
    if conv_meta.get("_ticket_full"):
        env["PIGEON_CONV_TICKET"] = str(conv_meta["_ticket_full"])
    if sec_uid:
        env["PIGEON_SECURITY_USER_ID"] = sec_uid
    env["PIGEON_MESSAGE_TEXT"] = text
    env["FEIGE_SESSION_JSON"] = str(session_path)
    env["PIGEON_RUST_SDK_NATIVE"] = layout["native_pkg"]
    env["PIGEON_RUST_SDK_API"] = layout["api_js"]
    env["PIGEON_RUST_SDK_NODE_MODULES"] = layout["node_modules"]
    env["NODE_PATH"] = layout["node_modules"]
    env.setdefault("NO_PROXY", "*")
    env.setdefault("no_proxy", "*")
    env["HTTP_PROXY"] = ""
    env["HTTPS_PROXY"] = ""
    env["ALL_PROXY"] = ""
    env["WINHTTP_PROXY"] = ""
    env["WINHTTP_PROXY_BYPASS"] = "*"
    env.setdefault("PIGEON_STANDALONE", "1")
    env.setdefault("PIGEON_WS_HOST", "jinritemai")
    feige_install = Path(r"E:\feige-electron\抖店工作台\1.1.7")
    if feige_install.is_dir():
        env.setdefault("PIGEON_FEIGE_INSTALL", str(feige_install))

    if not INVOKE_SCRIPT.is_file():
        report["ok"] = False
        report["error"] = f"missing script: {INVOKE_SCRIPT}"
        return report

    proc = subprocess.run(
        ["node", str(INVOKE_SCRIPT)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
    )
    report = _parse_invoke_result(session, report, proc)

    steps = ((report.get("node") or {}).get("steps") or {})
    if not report.get("ingested_classes") and steps.get("ws_error_push") and not os.environ.get("PIGEON_USE_LIVE_RS_SDK"):
        live_env = env.copy()
        live_env["PIGEON_USE_LIVE_RS_SDK"] = "1"
        proc2 = subprocess.run(
            ["node", str(INVOKE_SCRIPT)],
            cwd=str(ROOT),
            env=live_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
        report2 = _parse_invoke_result(session, dict(report), proc2)
        report2["retry"] = "live_rs_sdk"
        if report2.get("ingested_classes") or report2.get("ok"):
            return report2

    return report


def _parse_invoke_result(session, report: dict[str, Any], proc: subprocess.CompletedProcess) -> dict[str, Any]:
    report["node_exit"] = proc.returncode
    if proc.stderr:
        report["node_stderr"] = proc.stderr[:1500]

    try:
        node = json.loads(proc.stdout.strip()) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        node = {}
        report["node_raw"] = (proc.stdout or "")[:3000]
    report["node"] = node

    steps = (node.get("steps") or {})
    create = steps.get("createMessage") or {}
    inner_hex = (
        node.get("inner_169_hex")
        or create.get("inner_169_hex")
        or (steps.get("cloudSendMessage") or {}).get("inner_169_hex")
        or (steps.get("sendWithCreate") or {}).get("inner_169_hex")
        or (steps.get("set_flight_inflight") or {}).get("inner_169_hex")
        or (steps.get("setFlightInflight") or {}).get("inner_169_hex")
    )
    inner_via = node.get("inner_via") or create.get("inner_via")

    cap = ROOT / "analysis" / "feige_push_capture.bin"
    all_cap = ROOT / "analysis" / "feige_push_all.bin"

    if not inner_hex:
        for cap_path in (all_cap, cap):
            if not cap_path.is_file() or cap_path.stat().st_size <= 500:
                continue
            try:
                from pigeon_protocol.ws_sign import locate_signature_region
                from pigeon_protocol.ws_sign_decode import decode_blob

                raw = cap_path.read_bytes()
                region = locate_signature_region(raw)
                if region:
                    inner = decode_blob(region.blob)
                    if len(inner) == 169:
                        inner_hex = inner.hex()
                        inner_via = f"push_capture_{cap_path.name}"
                        report["push_capture"] = {
                            "len": len(raw),
                            "frame_len": region.frame_len,
                            "path": cap_path.name,
                        }
                        break
            except Exception as exc:
                report.setdefault("push_capture_errors", []).append(str(exc)[:120])

    if not inner_hex:
        try:
            from pigeon_protocol.foundation.ws_inner_bootstrap import scan_binary_for_inners

            for cap_path in (all_cap, cap):
                if not cap_path.is_file():
                    continue
                for hit in scan_binary_for_inners(cap_path.read_bytes()):
                    hx = str(hit.get("inner_hex") or "")
                    if len(hx) == 338:
                        inner_hex = hx
                        inner_via = f"push_inner_scan_{cap_path.name}"
                        break
                if inner_hex:
                    break
        except Exception as exc:
            report["push_scan_error"] = str(exc)[:200]

    if inner_hex:
        report["inner_via"] = inner_via
        report["ingested_classes"] = _ingest_inner_hex(session, inner_hex)
        report["ok"] = True
        report["via"] = inner_via or "rust_sdk_11327"
    else:
        try:
            from pigeon_protocol.foundation.python_ws_hybrid_inner import hybrid_seed_after_rust

            hybrid = hybrid_seed_after_rust(session, report)
            report["hybrid"] = hybrid
            if hybrid.get("ingested_classes"):
                report["ingested_classes"] = hybrid["ingested_classes"]
            if hybrid.get("ok"):
                report["ok"] = True
                report["via"] = hybrid.get("via", "hybrid")
        except Exception as exc:
            report["hybrid_error"] = str(exc)[:200]

        if not report.get("ok"):
            report["ok"] = bool(node.get("ok")) and bool(create.get("resp_len"))
            report["via"] = report.get("via") or "rust_sdk_partial"
            if create.get("error"):
                report["error"] = str(create.get("error"))[:200]
            elif (steps.get("cloudSendMessage") or {}).get("error"):
                report["error"] = str(steps["cloudSendMessage"]["error"])[:200]

    if report.get("ok") and report.get("ingested_classes"):
        try:
            from pigeon_protocol.foundation.ws_inner_normalize import normalize_session_inners
            from pigeon_protocol.session_portable import sync_portable_inner_sidecar

            normalize_session_inners(session, persist=True)
            sync_portable_inner_sidecar(session, force=True)
        except Exception as exc:
            logger.debug("post-ingest normalize: %s", exc)

    return report


def rust_sdk_seed_send_inner(session, *, text: str = "好") -> dict[str, Any]:
    """Best-effort 169B inner via Rust SDK (pure HTTP session, no CDP)."""
    from pigeon_protocol.foundation.rust_sdk_paths import rust_sdk_layout

    if not rust_sdk_layout().get("ok"):
        return {"ok": False, "skipped": "rust-sdk bundle not exported"}
    try:
        return invoke_create_message(session, text=text)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "rust sdk invoke timeout"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
