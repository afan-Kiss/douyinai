"""Resolve IM accessToken (createUser 11200 UUID) for edbX envelope / trailer derivation."""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("pigeon.im_access_token")

ROOT = Path(__file__).resolve().parents[3]
INVOKE_JSON = ROOT / "analysis" / "feige_rust_invoke_latest.json"
CREATE_USER_ONLY = ROOT / "scripts" / "feige_invoke_create_message.mjs"
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)


def _session_extra(session) -> dict[str, Any]:
    extra = getattr(session, "extra", None)
    if extra is None:
        extra = {}
        session.extra = extra
    return extra


def _persist_token(session, token: str, *, source: str) -> None:
    extra = _session_extra(session)
    extra["im_access_token"] = token
    extra["im_access_token_source"] = source
    try:
        from pigeon_protocol.session import save_session

        save_session(session)
    except Exception as exc:
        logger.debug("save_session token: %s", exc)


def _scan_invoke_json(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        raw = path.read_bytes()
        if raw[:2] == b"\xff\xfe" or raw[:2] == b"\xfe\xff":
            text = raw.decode("utf-16", errors="replace")
        else:
            text = raw.decode("utf-8", errors="replace")
        doc = json.loads(text)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return ""
    cu = (doc.get("steps") or {}).get("createUser") or {}
    tok = str(cu.get("access_token_full") or "")
    if tok and "..." not in tok and UUID_RE.fullmatch(tok):
        return tok
    for step in (doc.get("steps") or {}).values():
        if not isinstance(step, dict):
            continue
        for key, val in step.items():
            if "access_token" in key and isinstance(val, str) and UUID_RE.fullmatch(val):
                return val
    return ""


def resolve_im_access_token(session, *, allow_node: bool = True) -> tuple[str, str]:
    """
    Return (accessToken UUID, source).

    Sources: session.extra → analysis invoke dumps → minimal Node createUser (11200).
    """
    extra = _session_extra(session)
    cached = str(extra.get("im_access_token") or "")
    if cached and UUID_RE.fullmatch(cached):
        return cached, str(extra.get("im_access_token_source") or "session.extra")

    for path in (
        INVOKE_JSON,
        ROOT / "analysis" / "invoke_after_qr.json",
        ROOT / "analysis" / "last_invoke_jinritemai.json",
    ):
        tok = _scan_invoke_json(path)
        if tok:
            _persist_token(session, tok, source=f"invoke_json:{path.name}")
            return tok, f"invoke_json:{path.name}"

    if allow_node and CREATE_USER_ONLY.is_file():
        try:
            import os

            env = os.environ.copy()
            env["PIGEON_CREATE_USER_ONLY"] = "1"
            env.setdefault("PIGEON_STANDALONE", "1")
            env.setdefault("PIGEON_WS_HOST", "jinritemai")
            proc = subprocess.run(
                ["node", str(CREATE_USER_ONLY)],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
            )
            out_path = ROOT / "analysis" / "feige_create_user_latest.json"
            if proc.stdout.strip():
                out_path.write_text(proc.stdout.strip(), encoding="utf-8")
                doc = json.loads(proc.stdout.strip())
                tok = str(doc.get("access_token") or "")
                if tok and UUID_RE.fullmatch(tok):
                    _persist_token(session, tok, source="node_create_user")
                    return tok, "node_create_user"
        except Exception as exc:
            logger.debug("createUser only: %s", exc)

    return "", "missing"
