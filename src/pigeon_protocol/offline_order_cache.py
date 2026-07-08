"""Offline order cache — export via CDP bootstrap, read in PIGEON_STANDALONE=1."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pigeon_protocol.models import OrderContext
from pigeon_protocol.order_parse import parse_order_response


def _orders_dir() -> Path:
    from pigeon_protocol.account_context import bundle_dir

    return bundle_dir() / "orders"


def cache_path(security_user_id: str) -> Path:
    safe = security_user_id[:24].replace("/", "_")
    return _orders_dir() / f"{safe}.json"


def save_order_cache(security_user_id: str, raw: dict[str, Any], *, source: str = "cdp/bootstrap") -> Path:
    orders_dir = _orders_dir()
    orders_dir.mkdir(parents=True, exist_ok=True)
    ctx = parse_order_response(raw, source=source)
    payload = {
        "security_user_id": security_user_id,
        "source": source,
        "has_order": ctx.has_order,
        "summary": ctx.summary,
        "orders": ctx.orders,
        "raw": raw,
    }
    path = cache_path(security_user_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_order_cache(security_user_id: str) -> OrderContext | None:
    path = cache_path(security_user_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return OrderContext(
        has_order=bool(data.get("has_order")),
        orders=list(data.get("orders") or []),
        summary=str(data.get("summary") or ""),
        source=f"offline/cache/{path.name}",
        raw=data.get("raw") if isinstance(data.get("raw"), dict) else {"cached": data},
    )
