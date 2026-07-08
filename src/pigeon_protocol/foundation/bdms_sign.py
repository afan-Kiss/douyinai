"""Unified bdms / a_bogus signing — single entry for all backstage HTTP."""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from pigeon_protocol.foundation.types import BdmsSignResult
from pigeon_protocol.sign import SIGN_KEYS, parse_sign_tokens

logger = logging.getLogger("pigeon.foundation.bdms")

ROOT = Path(__file__).resolve().parents[3]
FETCH_SCRIPT = ROOT / "scripts" / "run_bdms_fetch.mjs"


def node_available() -> bool:
    return FETCH_SCRIPT.exists()


def python_abogus_available() -> bool:
    try:
        from pigeon_protocol.foundation.bdms_abogus import FeigeABogus

        probe = FeigeABogus().sign_query("device_platform=web&aid=1383")
        return len(probe) >= 100
    except Exception:
        return False


def sign_available() -> bool:
    """True when at least one offline signing backend can produce a_bogus."""
    from pigeon_protocol.pure_config import node_sign_allowed

    node_ok = node_available() and node_sign_allowed()
    return node_ok or python_abogus_available()


def best_signed_url(signed_payload: dict[str, Any], *, fallback: str) -> str:
    """Pick URL from node spy output — never rebuild query (breaks a_bogus)."""
    capture = signed_payload.get("capture") if isinstance(signed_payload.get("capture"), dict) else {}
    for key in ("requestUrl", "responseUrl", "signedUrl"):
        val = capture.get(key) if key != "signedUrl" else signed_payload.get(key)
        if val and "a_bogus=" in str(val):
            return str(val)
    if signed_payload.get("signedUrl"):
        return str(signed_payload["signedUrl"])
    tokens = extract_tokens(signed_payload, fallback=fallback)
    if tokens.get("a_bogus"):
        from pigeon_protocol.sign import apply_sign_tokens

        return apply_sign_tokens(fallback, tokens)
    return fallback


def extract_tokens(signed_payload: dict[str, Any], *, fallback: str = "") -> dict[str, str]:
    for src in (
        best_signed_url(signed_payload, fallback=fallback),
        signed_payload.get("signedUrl") or "",
        fallback,
    ):
        tokens = parse_sign_tokens(str(src))
        if tokens.get("a_bogus"):
            return tokens
    tokens = {k: str(signed_payload[k]) for k in SIGN_KEYS if signed_payload.get(k)}
    return tokens


def _default_prefer_python() -> bool:
    from pigeon_protocol.pure_config import prefer_python_abogus

    return prefer_python_abogus()


def _sign_python(unsigned_url: str, *, method: str, body_str: str) -> BdmsSignResult | None:
    try:
        from pigeon_protocol.foundation.bdms_abogus import sign_url_query
        from pigeon_protocol.session import load_session

        session = load_session()
        signed_url, bogus = sign_url_query(
            unsigned_url,
            method=method,
            body=body_str,
            session=session,
        )
        tokens = extract_tokens({"signedUrl": signed_url, "a_bogus": bogus}, fallback=signed_url)
        if tokens.get("a_bogus"):
            return BdmsSignResult(
                ok=True,
                signed_url=signed_url,
                tokens=tokens,
                via="python_abogus",
                method=method.upper(),
            )
    except Exception as exc:
        logger.warning("python a_bogus failed: %s", exc)
    return None


def _sign_node(
    unsigned_url: str,
    *,
    method: str,
    body_str: str,
    timeout_sec: int,
) -> BdmsSignResult:
    from pigeon_protocol.foundation.bdms_node_daemon import sign_via_daemon
    from pigeon_protocol.process_guard import (
        NodeProcessLimitError,
        cleanup_dead_registered_processes,
        ensure_node_capacity,
        oneshot_node_fallback_enabled,
        register_child_process,
        unregister_child_process,
    )
    from pigeon_protocol.subprocess_util import popen_hidden

    raw = sign_via_daemon(unsigned_url, body=body_str, method=method, timeout_sec=float(timeout_sec))
    if raw and raw.get("a_bogus"):
        signed_url = str(raw.get("signedUrl") or unsigned_url)
        tokens = {k: str(raw[k]) for k in SIGN_KEYS if raw.get(k)}
        ok = bool(tokens.get("a_bogus") and tokens.get("msToken"))
        return BdmsSignResult(
            ok=ok or bool(raw.get("partial")),
            signed_url=signed_url,
            tokens=tokens,
            via="node_bdms_daemon" if ok else "node_bdms_daemon/partial",
            method=method.upper(),
            raw=raw,
            error="" if ok else "missing a_bogus/msToken",
        )

    if not oneshot_node_fallback_enabled():
        return BdmsSignResult(
            ok=False,
            signed_url=unsigned_url,
            error="Node 签名未就绪（daemon 不可用，one-shot fallback 已禁用）",
            method=method.upper(),
            via="node_bdms_daemon/unavailable",
        )

    if not ensure_node_capacity():
        cleanup_dead_registered_processes()
        if not ensure_node_capacity():
            return BdmsSignResult(
                ok=False,
                signed_url=unsigned_url,
                error="node process limit reached",
                method=method.upper(),
            )

    cmd = ["node", str(FETCH_SCRIPT), unsigned_url, body_str, method.upper()]
    proc = None
    stdout = ""
    stderr = ""
    try:
        proc = popen_hidden(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(ROOT),
        )
        if proc.pid:
            register_child_process("node", int(proc.pid), cmd)
        stdout, stderr = proc.communicate(timeout=timeout_sec)
    except NodeProcessLimitError:
        return BdmsSignResult(
            ok=False,
            signed_url=unsigned_url,
            error="node process limit reached",
            method=method.upper(),
        )
    except subprocess.TimeoutExpired:
        if proc:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
            if proc.pid:
                unregister_child_process(int(proc.pid))
        cleanup_dead_registered_processes()
        return BdmsSignResult(
            ok=False,
            signed_url=unsigned_url,
            error="node one-shot fallback timeout",
            method=method.upper(),
        )
    finally:
        cleanup_dead_registered_processes()

    if not (stdout or "").strip():
        return BdmsSignResult(
            ok=False,
            signed_url=unsigned_url,
            error=(stderr or stdout or "empty node output")[:400],
            method=method.upper(),
        )
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return BdmsSignResult(
            ok=False,
            signed_url=unsigned_url,
            error=f"invalid json: {exc}",
            method=method.upper(),
        )

    signed_url = best_signed_url(raw, fallback=unsigned_url)
    tokens = extract_tokens(raw, fallback=signed_url)
    ok = bool(tokens.get("a_bogus") and tokens.get("msToken"))
    return BdmsSignResult(
        ok=ok or bool(raw.get("partial")),
        signed_url=signed_url,
        tokens=tokens,
        via="node_bdms_oneshot",
        method=method.upper(),
        raw=raw,
        error="" if ok else "missing a_bogus/msToken",
    )


def sign_backstage_url(
    unsigned_url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout_sec: int = 45,
    prefer_python: bool | None = None,
) -> BdmsSignResult:
    """Sign backstage URL — Python a_bogus when Node absent; chain both on retry."""
    body_str = json.dumps(body, ensure_ascii=False, separators=(",", ":")) if body else ""
    use_python = _default_prefer_python() if prefer_python is None else prefer_python

    from pigeon_protocol.pure_config import node_sign_allowed

    node_ok = node_available() and node_sign_allowed()
    if use_python:
        order = (["python", "node"] if node_ok else ["python"])
    else:
        order = (["node", "python"] if node_ok else ["python"])

    for backend in order:
        if backend == "python":
            result = _sign_python(unsigned_url, method=method, body_str=body_str)
            if result and result.ok:
                return result
        elif backend == "node" and node_ok:
            result = _sign_node(unsigned_url, method=method, body_str=body_str, timeout_sec=timeout_sec)
            if result.ok:
                return result

    from pigeon_protocol.foundation.bdms_tokens import append_backstage_query_tokens
    from pigeon_protocol.session import load_session

    fallback_url = append_backstage_query_tokens(unsigned_url, load_session())
    return BdmsSignResult(
        ok=False,
        signed_url=fallback_url,
        error="sign backends exhausted",
        method=method.upper(),
    )


def persist_tokens_to_session(session, result: BdmsSignResult) -> None:
    if not result.tokens:
        return
    session.query_tokens.update(
        {k: result.tokens[k] for k in SIGN_KEYS if result.tokens.get(k)}
    )
    try:
        from pigeon_protocol.session import save_session

        save_session(session)
    except Exception as exc:
        logger.debug("persist tokens skipped: %s", exc)
