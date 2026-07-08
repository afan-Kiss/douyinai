"""Normalize session inner cache — unify per-class keys to live session inner."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("pigeon.ws_inner_normalize")


def dominant_session_inner(session) -> bytes | None:
    """Pick the most common 169B inner among send class keys."""
    from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache, inner_class_registry

    cached = _load_session_class_cache(session)
    reg = inner_class_registry()
    counts: dict[bytes, int] = {}
    for ic in reg.values():
        raw = cached.get(ic.class_id)
        if raw and len(raw) == 169:
            counts[raw] = counts.get(raw, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda x: x[1])[0]


def normalize_session_inners(session, *, persist: bool = True) -> dict[str, Any]:
    """
    If session uses unified inner (majority vote), mirror to every class_id key.
    Fixes stale pool entries left on A/B after CDP warm.
    """
    from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache, _store_session_class_inner, inner_class_registry

    cached = _load_session_class_cache(session)
    reg = inner_class_registry()
    dominant = dominant_session_inner(session)
    if not dominant:
        return {"ok": False, "reason": "no cached inners"}

    patched: list[str] = []
    for ic in reg.values():
        cur = cached.get(ic.class_id)
        if cur != dominant:
            _store_session_class_inner(session, ic.class_id, dominant)
            patched.append(ic.name)

    if persist and patched:
        from pigeon_protocol.session import save_session

        try:
            save_session(session)
        except Exception as exc:
            logger.debug("save_session after normalize: %s", exc)

    return {
        "ok": True,
        "dominant_header": dominant[:8].hex(),
        "patched_classes": patched,
        "unified": len(patched) == 0 or len(patched) == len(reg),
    }
