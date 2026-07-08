"""Import session + relay headers from Chrome HAR (post-login capture)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _header_list_to_dict(headers: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in headers or []:
        name = str(h.get("name") or "").strip()
        val = str(h.get("value") or "").strip()
        if name:
            out[name] = val
    return out


def _cookie_list_to_dict(cookies: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in cookies or []:
        name = str(c.get("name") or "").strip()
        val = str(c.get("value") or "").strip()
        if name and val:
            out[name] = val
    return out


def extract_from_har(har_path: Path) -> dict[str, Any]:
    har = json.loads(har_path.read_text(encoding="utf-8"))
    entries = (har.get("log") or {}).get("entries") or []

    best_order_headers: dict[str, str] | None = None
    best_conv_headers: dict[str, str] | None = None
    conv_list_template: dict[str, str] | None = None
    ws_urls: list[str] = []
    cookies: dict[str, str] = _cookie_list_to_dict((har.get("log") or {}).get("cookies") or [])

    for entry in entries:
        req = entry.get("request") or {}
        url = str(req.get("url") or "")
        cookies.update(_cookie_list_to_dict(req.get("cookies") or []))
        hdr = _header_list_to_dict(req.get("headers") or [])

        if url.startswith("wss://") and "jinritemai" in url and url not in ws_urls:
            ws_urls.append(url)

        if "order/query" in url and req.get("method") == "POST":
            if hdr.get("x-secsdk-csrf-token"):
                best_order_headers = hdr

        if "xundan_chat_list" in url and req.get("method") == "GET":
            if hdr.get("x-secsdk-csrf-token"):
                best_conv_headers = hdr
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(url).query)
            conv_list_template = {
                "_v": (qs.get("_v") or [""])[0],
                "queue_key": (qs.get("queue_key") or ["no_order"])[0],
                "page_size": (qs.get("page_size") or ["20"])[0],
            }
            if qs.get("security_uid_list") and (qs.get("security_uid_list") or [""])[0]:
                conv_list_template["security_uid_list"] = qs["security_uid_list"][0]

        if hdr.get("Cookie"):
            for part in hdr["Cookie"].split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    cookies[k.strip()] = v.strip()

    relay = best_order_headers or best_conv_headers
    return {
        "cookies": cookies,
        "ws_urls": ws_urls,
        "relayHeaders": relay,
        "csrfHeader": (relay or {}).get("x-secsdk-csrf-token"),
        "convListTemplate": conv_list_template,
        "entries": len(entries),
    }


def import_har_session(
    har_path: Path,
    *,
    merge: bool = True,
    run_parse: bool = True,
    refresh_csrf_after: bool = True,
) -> dict[str, Any]:
    """Full HAR import: session.json + captures + bdms_browser_env."""
    from pigeon_protocol.session import load_session, save_session, SessionState
    from pigeon_protocol.secsdk_csrf import refresh_relay_headers

    extracted = extract_from_har(har_path)
    session = load_session() if merge else SessionState()
    prev_cookie_count = len(session.cookies)
    if extracted["cookies"]:
        session.cookies.update(extracted["cookies"])
    for url in extracted["ws_urls"]:
        if url not in session.ws_urls:
            session.ws_urls.append(url)

    # URL query tokens (msToken/a_bogus) from HAR entries
    from urllib.parse import parse_qs, urlparse

    for key in ("verifyFp", "fp", "msToken", "a_bogus"):
        if session.query_tokens.get(key):
            continue
        har = json.loads(har_path.read_text(encoding="utf-8"))
        for entry in (har.get("log") or {}).get("entries") or []:
            url = str((entry.get("request") or {}).get("url") or "")
            if "jinritemai" not in url:
                continue
            qs = parse_qs(urlparse(url).query)
            if qs.get(key):
                session.query_tokens[key] = qs[key][0]
                break
    if extracted.get("relayHeaders"):
        for k in ("User-Agent", "x-secsdk-csrf-token", "Referer", "Origin"):
            v = extracted["relayHeaders"].get(k) or extracted["relayHeaders"].get(k.lower())
            if v:
                session.headers[k if k != "Referer" else "Referer"] = v
    session.notes.append(f"har import {har_path.name} ({extracted['entries']} entries)")
    save_session(session)

    warnings: list[str] = []
    if not extracted["cookies"]:
        warnings.append(
            "HAR 无 Cookie（Chrome 导出需勾选 Include cookies）；已保留现有 session 或使用 import-cookies"
        )

    capture_summary = None
    cookies_backup = dict(session.cookies) if merge else {}
    if run_parse and extracted.get("ws_urls"):
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "parse_har.py"), str(har_path)],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        if proc.stdout.strip():
            try:
                capture_summary = json.loads(proc.stdout)
            except json.JSONDecodeError:
                capture_summary = {"stdout": proc.stdout[:500]}

    # parse_har overwrites session.json — restore merged cookies when HAR had none
    if merge and not extracted["cookies"] and cookies_backup:
        session = load_session()
        session.cookies.update(cookies_backup)
    elif merge and prev_cookie_count and not extracted["cookies"]:
        session = load_session()
    save_session(session)
    env_path = ROOT / "analysis" / "bdms_browser_env.json"
    env: dict[str, Any] = {}
    if env_path.exists():
        try:
            env = json.loads(env_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    if extracted.get("relayHeaders"):
        drop = {"content-length", "host", ":authority", ":method", ":path", ":scheme"}
        env["relayHeaders"] = {k: v for k, v in extracted["relayHeaders"].items() if k.lower() not in drop}
        env["csrfHeader"] = extracted.get("csrfHeader")
        env["csrfToken"] = session.cookies.get("csrf_session_id") or ""
        env["relayHeadersTs"] = int(time.time())
        env["harSource"] = str(har_path.name)
        if extracted.get("convListTemplate"):
            env["convListTemplate"] = extracted["convListTemplate"]
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")

        bundle_env = __import__(
            "pigeon_protocol.account_context", fromlist=["bundle_file"]
        ).bundle_file("bdms_browser_env.json")
        bundle_env.parent.mkdir(parents=True, exist_ok=True)
        bundle_env.write_text(json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")
    elif extracted.get("convListTemplate"):
        from pigeon_protocol.account_context import analysis_env_file, bundle_file

        for env_path in (analysis_env_file(), bundle_file("bdms_browser_env.json")):
            env = {}
            if env_path.exists():
                try:
                    env = json.loads(env_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    pass
            env["convListTemplate"] = extracted["convListTemplate"]
            env["harSource"] = str(har_path.name)
            env_path.parent.mkdir(parents=True, exist_ok=True)
            env_path.write_text(json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")

    csrf_refreshed = False
    if refresh_csrf_after and session.cookie_header():
        try:
            refresh_relay_headers(session, persist=True)
            csrf_refreshed = True
        except Exception:
            pass

    return {
        "har": str(har_path),
        "cookies": len(session.cookies),
        "ws_urls": len(session.ws_urls),
        "has_relay_headers": bool(extracted.get("relayHeaders")),
        "has_conv_list_template": bool(extracted.get("convListTemplate")),
        "conv_list_template": extracted.get("convListTemplate"),
        "csrf_refreshed": csrf_refreshed,
        "warnings": warnings,
        "capture_summary": capture_summary,
        "session_file": str(
            __import__("pigeon_protocol.account_context", fromlist=["session_file"]).session_file()
        ),
    }
