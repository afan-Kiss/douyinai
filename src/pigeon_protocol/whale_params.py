"""Live whale `_v` for backstage URLs — shared by order/xundan/sign refresh."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode


def whale_v_for_session(session: Any | None = None, *, fallback: str = "1.0.1.7626") -> str:
    cached = ""
    if session is not None:
        cached = str((getattr(session, "query_tokens", None) or {}).get("whale_v") or "")
    if cached:
        return cached
    try:
        from pigeon_protocol.whale_version import resolve_whale_versions

        vers = resolve_whale_versions(session=session)
        v = str(vers.get("whale_v") or "")
        if v and session is not None:
            session.query_tokens["whale_v"] = v
            im = vers.get("im_pc_version")
            if im:
                session.query_tokens["im_pc_version"] = im
        return v or fallback
    except Exception:
        return fallback


def backstage_query_base(*, session: Any | None = None, extra: dict[str, str] | None = None) -> str:
    """Standard backstage query prefix with live `_v`."""
    params = {
        "biz_type": "4",
        "PIGEON_BIZ_TYPE": "2",
        "_pms": "1",
        "device_platform": "web",
        "FUSION": "true",
        "_v": whale_v_for_session(session),
    }
    if extra:
        params.update(extra)
    return urlencode(params)
