"""Validate session-scoped 169B inner cache consistency."""
from __future__ import annotations

from typing import Any


def session_inner_health(session) -> dict[str, Any]:
    """
    Check whether cached inners are ready for ComputedBlobStrategy.

    Flags:
    - stale_pool: class keys still point at old harvest headers (≠ live CDP)
    - unified: all 7 classes share identical 169B (current Feige session mode)
    """
    from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache, inner_class_registry

    cached = _load_session_class_cache(session)
    reg = inner_class_registry()
    class_rows: list[dict[str, Any]] = []
    headers: set[str] = set()
    bodies: set[str] = set()

    for ic in reg.values():
        inner = cached.get(ic.class_id)
        if not inner or len(inner) != 169:
            class_rows.append({"class": ic.name, "class_id": ic.class_id[:16], "cached": False})
            continue
        hdr = inner[:8].hex()
        body = inner[8:].hex()
        headers.add(hdr)
        bodies.add(body)
        class_rows.append(
            {
                "class": ic.name,
                "class_id": ic.class_id[:16],
                "cached": True,
                "header_hex": hdr,
            }
        )

    need = {ic.class_id for ic in reg.values()}
    have = {ic.class_id for ic in reg.values() if cached.get(ic.class_id)}
    unified = len(headers) == 1 and len(have) >= 7

    # Stale: multiple distinct headers when we expect unified session inner
    stale_pool = len(headers) > 1

    return {
        "ready": len(have) >= 4,
        "full": len(have) >= 7,
        "unified": unified,
        "stale_pool": stale_pool,
        "distinct_headers": sorted(headers),
        "cached_count": len(have),
        "classes": class_rows,
        "needs_cdp_warm": stale_pool or len(have) < 7,
    }


def ensure_fresh_session_inners(session, *, cdp_if_needed: bool = True) -> dict[str, Any]:
    """Refresh inners via Rust SDK (pure) or CDP warm when cache is incomplete."""
    from pigeon_protocol.pure_config import cdp_allowed

    health = session_inner_health(session)
    if not health.get("needs_cdp_warm"):
        return {"health": health, "refreshed": False}

    use_cdp = cdp_if_needed and cdp_allowed()
    if not use_cdp:
        try:
            from pigeon_protocol.foundation.rust_sdk_inner import rust_sdk_seed_send_inner

            rust = rust_sdk_seed_send_inner(session)
            health2 = session_inner_health(session)
            return {
                "health": health,
                "refreshed": bool(rust.get("ingested_classes")),
                "via": "rust_sdk",
                "rust": rust,
                "health_after": health2,
            }
        except Exception as exc:
            return {"health": health, "refreshed": False, "skipped": str(exc)[:120]}

    from pigeon_protocol.cdp_warm_inners import auto_warm_inners_if_needed

    warm = auto_warm_inners_if_needed(launch=True, background=False)
    health2 = session_inner_health(session)
    return {
        "health": health,
        "refreshed": bool(warm.get("ok")) and not warm.get("skipped"),
        "via": "cdp_warm",
        "warm": warm,
        "health_after": health2,
    }
