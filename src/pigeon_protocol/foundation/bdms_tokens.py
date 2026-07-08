"""Backstage query token append — mirrors bdms fn#107 (msToken + verifyFp)."""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

logger = logging.getLogger("pigeon.bdms_tokens")


def _env_paths():
    from pigeon_protocol.account_context import analysis_env_file, bundle_file

    return bundle_file("bdms_browser_env.json"), analysis_env_file()


def _load_browser_env() -> dict[str, Any]:
    for path in _env_paths():
        if not path.is_file():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def verify_fp_from_cookies(cookies: dict[str, str] | None) -> str:
    ck = cookies or {}
    return str(ck.get("s_v_web_id") or ck.get("verifyFp") or ck.get("fp") or "")


def backstage_query_tokens(session) -> dict[str, str]:
    """msToken + verifyFp/fp for backstage URL append (session + bundle env)."""
    env = _load_browser_env()
    cookies = getattr(session, "cookies", None) or {}
    q = getattr(session, "query_tokens", None) or {}

    out: dict[str, str] = {}
    ms = str(q.get("msToken") or cookies.get("msToken") or (env.get("localStorage") or {}).get("xmst") or "")
    if ms:
        out["msToken"] = ms

    fp = verify_fp_from_cookies(cookies) or str(q.get("verifyFp") or q.get("fp") or "")
    if fp:
        out["verifyFp"] = fp
        out["fp"] = fp
    return out


def append_backstage_query_tokens(url: str, session) -> str:
    """Append msToken/verifyFp to unsigned backstage URL when missing."""
    tokens = backstage_query_tokens(session)
    if not tokens:
        return url
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key in ("msToken", "verifyFp", "fp"):
        val = tokens.get(key)
        if val and not qs.get(key):
            qs[key] = val
    return urlunparse(parsed._replace(query=urlencode(qs)))


def persist_ms_token_from_response(session, headers: dict[str, Any] | None) -> bool:
    """Write x-ms-token response header back to session.query_tokens."""
    if not headers or session is None:
        return False
    new_ms = headers.get("x-ms-token") or headers.get("X-Ms-Token") or headers.get("X-MS-TOKEN")
    if not isinstance(new_ms, str) or not new_ms.strip():
        return False
    q = getattr(session, "query_tokens", None)
    if q is None:
        return False
    if q.get("msToken") == new_ms.strip():
        return False
    q["msToken"] = new_ms.strip()
    try:
        from pigeon_protocol.session import save_session

        save_session(session)
    except Exception as exc:
        logger.debug("persist msToken: %s", exc)
    return True
