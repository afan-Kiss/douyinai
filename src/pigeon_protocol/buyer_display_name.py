"""Buyer display name extraction and bad-name filtering for conversation lists."""
from __future__ import annotations

import re
from typing import Any

_BAD_EXACT = frozenset(
    {
        "",
        "其他",
        "未知",
        "未知买家",
        "站内push推送",
        "站内push",
        "抖音",
        "今日头条",
        "火山",
        "来源",
        "用户",
        "买家",
        "暂无",
        "null",
        "none",
        "undefined",
    }
)

FORBIDDEN_NAME_KEYS = frozenset(
    {
        "user_from_desc",
        "source",
        "from",
        "channel",
        "from_desc",
        "user_type",
        "type",
        "category",
        "tag",
        "label",
        "desc",
        "description",
    }
)

HIGH_PRIORITY_KEYS = (
    "nick_name",
    "nickname",
    "nickName",
    "user_name",
    "userName",
    "screen_name",
    "screenName",
    "display_name",
    "displayName",
    "buyer_name",
    "buyerName",
    "customer_name",
    "customerName",
    "contact_name",
    "contactName",
    "remark_name",
    "remarkName",
    "avatar_name",
    "avatarName",
    "show_name",
    "showName",
    "real_name",
    "realName",
    "uname",
    "cname",
    "remark",
)

MEDIUM_PRIORITY_KEYS = ("name",)


def is_bad_display_name(name: str) -> bool:
    n = str(name or "").strip()
    if not n:
        return True
    if n in _BAD_EXACT:
        return True
    lower = n.lower()
    if lower in _BAD_EXACT:
        return True
    if "fallback" in lower:
        return True
    if n.isdigit() and len(n) < 4:
        return True
    if re.fullmatch(r"\d+", n) and len(n) < 4:
        return True
    return False


def _key_priority(key: str) -> int | None:
    if key in HIGH_PRIORITY_KEYS:
        return 1
    if key in MEDIUM_PRIORITY_KEYS:
        return 2
    return None


def extract_buyer_name_from_obj(obj: Any) -> str:
    candidates: list[tuple[int, str]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                k = str(key)
                if k in FORBIDDEN_NAME_KEYS:
                    if isinstance(val, (dict, list)):
                        walk(val)
                    continue
                pri = _key_priority(k)
                if pri is not None:
                    if isinstance(val, str):
                        text = val.strip()
                    elif isinstance(val, (int, float)) and not isinstance(val, bool):
                        text = str(val).strip()
                    else:
                        text = ""
                    if text and not is_bad_display_name(text):
                        candidates.append((pri, text))
                elif isinstance(val, (dict, list)):
                    walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    if not candidates:
        return ""
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def buyer_label_from_uid(uid: str) -> str:
    u = str(uid or "").strip()
    if len(u) >= 6:
        return f"买家{u[-6:]}"
    if u:
        return f"买家{u}"
    return "未知买家"


def _get_nested(d: dict[str, Any], *paths: tuple[str, ...]) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for path in paths:
        cur: Any = d
        ok = True
        for part in path:
            if not isinstance(cur, dict):
                ok = False
                break
            cur = cur.get(part)
        if ok and cur is not None:
            label = ".".join(path)
            out.append((label, cur))
    return out


def extract_conversation_display_name(
    it: dict[str, Any],
    msg_body: dict[str, Any] | None = None,
    ext: dict[str, Any] | None = None,
) -> tuple[str, str]:
    msg_body = msg_body or {}
    ext = ext if isinstance(ext, dict) else {}
    it_ext = it.get("ext") if isinstance(it.get("ext"), dict) else {}

    field_sources: list[tuple[str, Any]] = []
    for label, val in _get_nested(
        it,
        ("nick_name",),
        ("nickname",),
        ("user_name",),
        ("buyer_name",),
        ("customer_name",),
        ("contact_name",),
        ("remark_name",),
        ("display_name",),
        ("user_info", "nick_name"),
        ("user_info", "nickname"),
        ("user_info", "user_name"),
        ("user_info", "display_name"),
        ("user", "nick_name"),
        ("user", "nickname"),
        ("user", "user_name"),
        ("base_user_info", "nick_name"),
        ("base_user_info", "nickname"),
        ("base_user_info", "user_name"),
    ):
        field_sources.append((f"it.{label}", val))

    for label, val in _get_nested(
        it_ext,
        ("nick_name",),
        ("nickname",),
        ("user_name",),
        ("uname",),
        ("cname",),
    ):
        field_sources.append((f"it.ext.{label}", val))

    for label, val in _get_nested(
        ext,
        ("nick_name",),
        ("nickname",),
        ("user_name",),
        ("uname",),
        ("cname",),
    ):
        field_sources.append((f"ext.{label}", val))

    field_sources.append(("it.title", it.get("title")))

    for source, val in field_sources:
        if val is None:
            continue
        text = str(val).strip()
        if text and not is_bad_display_name(text):
            return text, source

    nested = extract_buyer_name_from_obj(it)
    if nested:
        return nested, "nested"

    return "", ""


def resolve_item_display_name(item: dict[str, Any]) -> tuple[str, str]:
    uid = str(item.get("security_user_id") or item.get("security_uid") or "")

    card = item.get("card")
    if isinstance(card, dict):
        nested = extract_buyer_name_from_obj(card)
        if nested:
            return nested, "card"

    last_msg = item.get("last_history_msg") if isinstance(item.get("last_history_msg"), dict) else {}
    msg_body = last_msg.get("message_body") if isinstance(last_msg.get("message_body"), dict) else {}
    ext = msg_body.get("ext") if isinstance(msg_body.get("ext"), dict) else {}
    if not ext and isinstance(item.get("ext"), dict):
        ext = item["ext"]

    name, src = extract_conversation_display_name(item, msg_body, ext)
    if name:
        return name, src

    for key in ("display_name", "buyer_name", "name", "nickname", "nick_name", "user_name"):
        val = item.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text and not is_bad_display_name(text):
            return text, f"item.{key}"

    if uid:
        return buyer_label_from_uid(uid), "uid_tail"
    return "未知买家", "unknown"


def normalize_conversation_item(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    uid = str(out.get("security_user_id") or out.get("security_uid") or "")
    name, name_source = resolve_item_display_name(out)
    if is_bad_display_name(name):
        name = buyer_label_from_uid(uid) if uid else "未知买家"
        name_source = "uid_tail"
    out["name"] = name
    out["buyer_name"] = name
    out["display_name"] = name
    out["name_source"] = name_source or str(out.get("name_source") or "")
    preview = sanitize_conv_preview(str(out.get("preview") or ""))
    if preview:
        out["preview"] = preview[:120]
    return out


def normalize_conversation_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_conversation_item(it) for it in items if isinstance(it, dict)]


def enrich_items_with_user_card(
    session,
    items: list[dict[str, Any]],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    from pigeon_protocol.conv_list_fallback import _user_card_hint

    out: list[dict[str, Any]] = []
    enriched = 0
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        uid = str(item.get("security_user_id") or item.get("security_uid") or "")
        current = str(item.get("display_name") or item.get("buyer_name") or item.get("name") or "")
        uid_label = buyer_label_from_uid(uid) if uid else ""
        needs_card = (
            bool(uid)
            and enriched < limit
            and (
                not current
                or is_bad_display_name(current)
                or current == uid_label
                or current.replace(" ", "") == uid_label
            )
        )
        if needs_card:
            hint = _user_card_hint(session, uid)
            card_name = str(hint.get("name") or "").strip()
            if card_name and not is_bad_display_name(card_name):
                item["name"] = card_name
                item["buyer_name"] = card_name
                item["display_name"] = card_name
                item["name_source"] = "user_card"
                if hint.get("buyer_source"):
                    item["buyer_source"] = str(hint.get("buyer_source") or "")
                if isinstance(hint.get("card"), dict):
                    item["card"] = hint["card"]
                enriched += 1
        out.append(normalize_conversation_item(item))
    return out


def sanitize_conv_preview(preview: str) -> str:
    p = str(preview or "").strip()
    if not p:
        return ""
    if re.search(r"已知买家\s*[（(].*fallback", p, re.I):
        return "已知买家"
    if re.search(r"xundan\s*11001\s*fallback", p, re.I):
        return "已知买家"
    if "fallback" in p.lower() and "已知买家" in p:
        return "已知买家"
    return p
