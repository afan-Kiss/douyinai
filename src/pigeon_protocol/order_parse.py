from __future__ import annotations

from typing import Any

from pigeon_protocol.models import OrderContext


def parse_order_response(result: dict[str, Any], *, source: str = "backstage/order/query") -> OrderContext:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    orders = data.get("data") if isinstance(data.get("data"), list) else []
    cd = data.get("componentized_data") or {}
    if cd and not orders:
        inner = cd.get("data") or {}
        if isinstance(inner, dict):
            for v in inner.values():
                if isinstance(v, dict) and (v.get("order_id") or "shop_order" in str(v)):
                    orders.append(v)
    has_order = bool(orders) or bool(cd)
    total = data.get("total", len(orders))
    code = str(data.get("code", "0"))
    if code not in ("0", "0.0") and not has_order:
        summary = str(data.get("msg") or result.get("error") or "订单查询失败")
    elif has_order:
        summary = f"共 {total} 单"
    else:
        summary = "当前买家暂无订单"
    return OrderContext(
        has_order=has_order,
        orders=orders if isinstance(orders, list) else [],
        summary=summary,
        source=source,
        raw=result,
    )
