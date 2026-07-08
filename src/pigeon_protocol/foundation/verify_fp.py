"""Offline verifyFp generation — mirrors secsdk-captcha cookie fp (424.js module m)."""
from __future__ import annotations

import random
import time

_ALPHANUM = list("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


def _js_date_now_base36() -> str:
    n = int(time.time() * 1000)
    if n == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out: list[str] = []
    while n:
        n, rem = divmod(n, 36)
        out.append(digits[rem])
    return "".join(reversed(out))


def generate_verify_fp(*, seed: int | None = None) -> str:
    """Generate `verify_{ts36}_{uuid-like}` fingerprint."""
    rng = random.Random(seed)
    t = len(_ALPHANUM)
    parts: list[str | None] = [None] * 36
    parts[8] = parts[13] = parts[18] = parts[23] = "_"
    parts[14] = "4"
    for idx in range(36):
        if parts[idx] is not None:
            continue
        pick = rng.randrange(t)
        if idx == 19:
            pick = (3 & pick) | 8
        parts[idx] = _ALPHANUM[pick]
    return f"verify_{_js_date_now_base36()}_{''.join(parts)}"


def refresh_session_verify_fp(session, *, force: bool = False) -> str:
    """Set fresh verifyFp/fp on session (+ s_v_web_id cookie when forced)."""
    existing = str(session.cookies.get("s_v_web_id") or session.query_tokens.get("verifyFp") or "")
    if existing.startswith("verify_") and not force:
        session.query_tokens.setdefault("verifyFp", existing)
        session.query_tokens.setdefault("fp", existing)
        return existing

    fp = generate_verify_fp()
    session.query_tokens["verifyFp"] = fp
    session.query_tokens["fp"] = fp
    if force:
        session.cookies["s_v_web_id"] = fp
    return fp
