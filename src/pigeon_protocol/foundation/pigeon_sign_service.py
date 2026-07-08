"""pigeon_sign resolution — protobuf extract + token/sign pairing (no HTML scrape)."""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from pigeon_protocol.init_body import PIGEON_SIGN_MARKER, _find_string_after_marker
from pigeon_protocol.ws_url_builder import PIGEON_SIGN_RE

logger = logging.getLogger("pigeon.pigeon_sign")

MIHA_RE = PIGEON_SIGN_RE


def extract_sign_from_protobuf(data: bytes) -> str:
    """Read pigeon_sign string field from protobuf body (init/context templates)."""
    loc = _find_string_after_marker(bytearray(data), PIGEON_SIGN_MARKER)
    if not loc:
        return ""
    _, val_start, val_end = loc
    try:
        return data[val_start:val_end].decode("utf-8")
    except UnicodeDecodeError:
        return ""


def extract_sign_from_text(text: str) -> str:
    signs = MIHA_RE.findall(text or "")
    return max(signs, key=len) if signs else ""


def token_sign_pairs(session) -> dict[str, str]:
    """Build token → pigeon_sign map from historical WS URLs."""
    out: dict[str, str] = {}
    for url in session.ws_urls or []:
        if "ws.fxg.jinritemai.com" not in url:
            continue
        q = parse_qs(urlparse(url).query)
        tok = (q.get("token") or [""])[0]
        sign = unquote((q.get("pigeon_sign") or [""])[0])
        if tok and sign and len(sign) > 80:
            out[tok] = sign
    return out


def sign_for_token(session, token: str = "") -> str:
    """Resolve pigeon_sign for a WS token (paired URL history first)."""
    tok = token or str(session.query_tokens.get("token") or "")
    if tok:
        paired = token_sign_pairs(session).get(tok)
        if paired:
            return paired
    return str(session.query_tokens.get("pigeon_sign") or "")


def bootstrap_sign_from_templates(session) -> dict[str, Any]:
    """
    Pure HTTP/bootstrap path for pigeon_sign — no workspace HTML scrape.
    Sources: init POST template, context template, ws_url token pairs.
    """
    from pigeon_protocol.pure_config import BUNDLE_CONTEXT_BODY, BUNDLE_INIT_BODY

    report: dict[str, Any] = {"sources": []}
    candidates: list[str] = []

    for label, path in (
        ("init_body", BUNDLE_INIT_BODY),
        ("context_body", BUNDLE_CONTEXT_BODY),
    ):
        if not path.is_file():
            continue
        sign = extract_sign_from_protobuf(path.read_bytes())
        if sign:
            candidates.append(sign)
            report["sources"].append(f"{label}:protobuf")

    paired = token_sign_pairs(session)
    if paired:
        report["sources"].append(f"ws_pairs:{len(paired)}")
        candidates.extend(paired.values())

    existing = str(session.query_tokens.get("pigeon_sign") or "")
    if existing:
        candidates.append(existing)

    if not candidates:
        report["ok"] = False
        return report

    # Prefer longest valid MIHA ticket
    best = max(candidates, key=len)
    if len(best) > 80 and session.query_tokens.get("pigeon_sign") != best:
        session.query_tokens["pigeon_sign"] = best
        report["applied"] = "pigeon_sign"
    report["ok"] = bool(session.query_tokens.get("pigeon_sign"))
    report["sign_len"] = len(str(session.query_tokens.get("pigeon_sign") or ""))
    return report


def ensure_pigeon_sign(session, *, persist: bool = False) -> dict[str, Any]:
    """
    Ensure session has pigeon_sign before WS URL synthesis / pigeon_im calls.
    Order: token pair → template protobuf → existing query_tokens.
    """
    tok = str(session.query_tokens.get("token") or "")
    paired = sign_for_token(session, tok)
    if paired:
        session.query_tokens["pigeon_sign"] = paired
        return {"ok": True, "via": "token_pair", "sign_len": len(paired)}

    boot = bootstrap_sign_from_templates(session)
    if boot.get("ok"):
        boot["via"] = "templates"
        if persist:
            _save(session)
        return boot

    # Last resort: scan init HTTP response binary (latin-1 MIHA)
    try:
        from pathlib import Path

        resp = Path(__file__).resolve().parents[3] / "standalone_bundle" / "get_message_by_init_response.bin"
        if resp.is_file():
            text = resp.read_bytes().decode("latin-1", errors="ignore")
            sign = extract_sign_from_text(text)
            if sign:
                session.query_tokens["pigeon_sign"] = sign
                if persist:
                    _save(session)
                return {"ok": True, "via": "init_response_scan", "sign_len": len(sign)}
    except OSError as exc:
        logger.debug("init response sign scan: %s", exc)

    return {"ok": bool(session.query_tokens.get("pigeon_sign")), "via": "existing"}


def _save(session) -> None:
    try:
        from pigeon_protocol.session import save_session

        save_session(session)
    except OSError as exc:
        logger.debug("save_session pigeon_sign: %s", exc)
