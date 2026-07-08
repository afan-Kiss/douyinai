"""Parse backstage order/componentized_data into UI-friendly cards."""
from __future__ import annotations

import json
import re
from typing import Any


def _dig(obj: Any, *keys: str) -> Any:
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _fmt_money(val: Any) -> str:
    if val is None or val == "":
        return "—"
    try:
        n = float(val)
        if n > 1000:
            return f"¥{n / 100:.2f}"
        return f"¥{n:.2f}"
    except (TypeError, ValueError):
        s = str(val)
        return s if s.startswith("¥") else f"¥{s}"


def _fmt_ts_ms(ms: Any) -> str:
    if not ms:
        return ""
    try:
        import datetime

        ts = int(str(ms)[:13])
        if ts < 1_000_000_000_000:
            ts *= 1000
        return datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _collect_order_ids(structure: dict[str, list[str]]) -> list[str]:
    root = structure.get("root_1") or structure.get("root") or []
    return [x.replace("shop_order_", "") for x in root if str(x).startswith("shop_order_")]


def parse_componentized_orders(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn order API raw (with componentized_data) into display cards."""
    data_root = raw if isinstance(raw, dict) else {}
    inner = data_root.get("data") if isinstance(data_root.get("data"), dict) else data_root
    cd = inner.get("componentized_data") if isinstance(inner.get("componentized_data"), dict) else {}
    if not cd:
        cd = data_root.get("componentized_data") if isinstance(data_root.get("componentized_data"), dict) else {}

    comp_data = cd.get("data") if isinstance(cd.get("data"), dict) else {}
    hierarchy = cd.get("hierarchy") if isinstance(cd.get("hierarchy"), dict) else {}
    structure = hierarchy.get("structure") if isinstance(hierarchy.get("structure"), dict) else {}

    order_ids = _collect_order_ids(structure)
    if not order_ids:
        for key in comp_data:
            if str(key).startswith("shop_order_") and "container" not in key:
                oid = str(key).replace("shop_order_", "")
                if oid.isdigit():
                    order_ids.append(oid)

    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for oid in order_ids:
        if oid in seen:
            continue
        seen.add(oid)
        card = _parse_one_order(oid, comp_data)
        if card:
            cards.append(card)
    return cards


def _parse_one_order(order_id: str, comp_data: dict[str, Any]) -> dict[str, Any] | None:
    prefix = f"shop_order_{order_id}"
    base = comp_data.get(prefix) if isinstance(comp_data.get(prefix), dict) else {}
    base_fields = base.get("fields") if isinstance(base.get("fields"), dict) else {}

    header_key = f"shop_order_header_card_{order_id}"
    header = comp_data.get(header_key) if isinstance(comp_data.get(header_key), dict) else {}
    h_fields = header.get("fields") if isinstance(header.get("fields"), dict) else {}
    h_ext = h_fields.get("ext") if isinstance(h_fields.get("ext"), dict) else {}

    logistics_key = f"shop_order_info_logistics_2_{order_id}"
    logistics = comp_data.get(logistics_key) if isinstance(comp_data.get(logistics_key), dict) else {}
    l_fields = logistics.get("fields") if isinstance(logistics.get("fields"), dict) else {}

    ship_key = f"shop_info_logistics_time_sec_text_2_{order_id}"
    ship = comp_data.get(ship_key) if isinstance(comp_data.get(ship_key), dict) else {}
    s_fields = ship.get("fields") if isinstance(ship.get("fields"), dict) else {}

    sku_list = h_ext.get("sku_order_list") if isinstance(h_ext.get("sku_order_list"), list) else []
    products: list[dict[str, str]] = []
    for sku in sku_list:
        if not isinstance(sku, dict):
            continue
        products.append(
            {
                "name": str(sku.get("product_name") or sku.get("title") or ""),
                "image": str(sku.get("img") or sku.get("image") or ""),
                "quantity": str(sku.get("buy_num") or sku.get("num") or "1"),
            }
        )

    product_name = products[0]["name"] if products else ""
    amount = h_ext.get("actual_pay_amount_str") or h_ext.get("actual_pay_amount") or base_fields.get("pay_amount")
    status = (
        h_fields.get("order_status_desc")
        or base_fields.get("order_status_desc")
        or base_fields.get("order_status")
        or ""
    )

    logistics_text = (
        l_fields.get("logistics_desc")
        or l_fields.get("logistics_status_desc")
        or s_fields.get("logistics_time_sec_text")
        or s_fields.get("logistics_time_sec_hover")
        or ""
    )
    ship_time = s_fields.get("logistics_time_sec_text") or s_fields.get("exp_ship_time_text") or ""

    pay_time = _fmt_ts_ms(h_ext.get("pay_time") or h_ext.get("create_time") or base_fields.get("create_time"))

    after_sale = str(
        h_fields.get("after_sale_status_desc")
        or l_fields.get("after_sale_status_desc")
        or base_fields.get("after_sale_status_desc")
        or ""
    )

    return {
        "order_id": order_id,
        "product_name": product_name or f"订单 {order_id}",
        "products": products,
        "amount": _fmt_money(amount),
        "amount_raw": amount,
        "status": str(status or "—"),
        "pay_time": pay_time,
        "ship_time": str(ship_time),
        "logistics": str(logistics_text),
        "after_sale": after_sale,
        "is_after_sale": bool(after_sale and after_sale not in ("—", "", "无")),
    }


def enrich_order_context(order_ctx) -> dict[str, Any]:
    """Add `cards` to OrderContext dict without breaking existing fields."""
    from dataclasses import asdict, is_dataclass

    base = asdict(order_ctx) if is_dataclass(order_ctx) else dict(order_ctx)
    raw = base.get("raw") if isinstance(base.get("raw"), dict) else {}
    cards = parse_componentized_orders(raw)
    if not cards and isinstance(raw.get("data"), dict):
        cards = parse_componentized_orders(raw.get("data") or {})
    base["cards"] = cards
    if cards and not base.get("summary"):
        base["summary"] = f"共 {len(cards)} 单"
    return base
