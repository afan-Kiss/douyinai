from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pigeon_protocol.capture_loader import load_capture
from pigeon_protocol.config import LIVE_CAPTURES
from pigeon_protocol.models import ConversationContext, OrderContext


def _loads_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text or "{}")
    except Exception:
        return {}


def find_har_capture(root: Path, needle: str) -> dict[str, Any] | None:
    for path in sorted(root.glob("har_*_http_body.json"), reverse=True):
        try:
            event = load_capture(path)
        except Exception:
            continue
        if needle in str(event.get("url") or ""):
            return event
    return None


def order_from_har_for_user(
    security_user_id: str,
    root: Path | None = None,
) -> OrderContext | None:
    """Replay HAR order/query captures matching a specific buyer."""
    root = root or LIVE_CAPTURES / "from_har"
    best: OrderContext | None = None
    for path in sorted(root.glob("har_*_http_body.json"), reverse=True):
        try:
            event = load_capture(path)
        except Exception:
            continue
        if "order/query" not in str(event.get("url") or ""):
            continue
        post = _loads_json(str(event.get("post_data") or "{}"))
        if security_user_id and post.get("security_user_id") != security_user_id:
            continue
        data = _loads_json(str(event.get("response_body") or ""))
        total = int(data.get("total") or 0)
        cd = data.get("componentized_data") or {}
        orders = data.get("data") if isinstance(data.get("data"), list) else []
        if cd and not orders:
            inner = cd.get("data") or {}
            if isinstance(inner, dict):
                for v in inner.values():
                    if isinstance(v, dict) and (v.get("order_id") or "shop_order" in str(v)):
                        orders.append(v)
        has_order = bool(orders) or bool(cd)
        ctx = OrderContext(
            has_order=has_order,
            orders=orders if isinstance(orders, list) else [],
            summary=f"HAR replay total={total or len(orders)}",
            source="har/order/query",
            raw={"post": post, "response": data, "url": event.get("url"), "file": str(path.name)},
        )
        if has_order and (best is None or total > int((best.raw.get("response") or {}).get("total") or 0)):
            best = ctx
    return best


def order_from_har(root: Path | None = None) -> OrderContext | None:
    root = root or LIVE_CAPTURES / "from_har"
    best: OrderContext | None = None
    for path in sorted(root.glob("har_*_http_body.json")):
        try:
            event = load_capture(path)
        except Exception:
            continue
        if "order/query" not in str(event.get("url") or ""):
            continue
        data = _loads_json(str(event.get("response_body") or ""))
        post = _loads_json(str(event.get("post_data") or "{}"))
        total = int(data.get("total") or 0)
        cd = data.get("componentized_data") or {}
        orders = data.get("data") if isinstance(data.get("data"), list) else []
        if cd and not orders:
            inner = cd.get("data") or {}
            if isinstance(inner, dict):
                for v in inner.values():
                    if isinstance(v, dict) and (v.get("order_id") or "shop_order" in str(v)):
                        orders.append(v)
        has_order = bool(orders) or bool(cd)
        ctx = OrderContext(
            has_order=has_order,
            orders=orders if isinstance(orders, list) else [],
            summary=f"HAR replay total={total or len(orders)}",
            source="har/order/query",
            raw={"post": post, "response": data, "url": event.get("url")},
        )
        if has_order and (best is None or total > int((best.raw.get("response") or {}).get("total") or 0)):
            best = ctx
    return best


def context_from_har(root: Path | None = None) -> ConversationContext | None:
    root = root or LIVE_CAPTURES / "from_har"
    best: ConversationContext | None = None
    for needle in ("get_by_conversation", "get_user_message", "get_message_by_init"):
        for path in sorted(root.glob("har_*_http_body.json"), reverse=True):
            try:
                event = load_capture(path)
            except Exception:
                continue
            if needle not in str(event.get("url") or ""):
                continue
            if str(event.get("method") or "").upper() not in {"POST", "GET"}:
                continue
            data = _loads_json(str(event.get("response_body") or ""))
            post = _loads_json(str(event.get("post_data") or "{}"))
            messages: list[dict[str, Any]] = []
            payload = data.get("data") if isinstance(data.get("data"), dict) else data
            msg_list = payload.get("msg_body_list") or payload.get("messages") or data.get("msg_body_list")
            if isinstance(msg_list, list):
                for item in msg_list:
                    if not isinstance(item, dict):
                        continue
                    ext = item.get("ext") if isinstance(item.get("ext"), dict) else {}
                    role = "customer" if str(item.get("sender_role") or ext.get("sender_role")) == "1" else "service"
                    messages.append(
                        {
                            "role": role,
                            "text": str(item.get("content") or "").strip(),
                            "time": str(item.get("create_time") or ""),
                        }
                    )
            ctx = ConversationContext(
                conversation_id=str(post.get("conversation_id") or post.get("talk_id") or ""),
                security_user_id=str(post.get("security_user_id") or ""),
                shop_id=str(post.get("shop_id") or ""),
                buyer_name="",
                messages=[m for m in messages if m.get("text")],
                source=f"har/{needle}",
                raw={"post": post, "response": data, "url": event.get("url")},
            )
            if len(ctx.messages) > len(best.messages if best else []):
                best = ctx
    return best
