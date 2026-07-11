"""Buyer display name extraction and bad-name filtering for conversation lists."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("pigeon.buyer_display_name")

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


def is_uid_fallback_label(name: str, uid: str = "") -> bool:
    n = str(name or "").strip().replace(" ", "")
    if not n:
        return False
    if re.fullmatch(r"买家[A-Za-z0-9_-]{4,10}", n):
        return True
    u = str(uid or "").strip()
    if u and n == buyer_label_from_uid(u).replace(" ", ""):
        return True
    return False


def user_card_has_buyer_nick(inner: dict[str, Any] | None) -> bool:
    if not isinstance(inner, dict):
        return False
    user_info = inner.get("user_info") if isinstance(inner.get("user_info"), dict) else {}
    for key in ("nick_name", "nickname", "nickName", "uname", "user_name", "userName", "display_name", "displayName"):
        for blob in (user_info, inner):
            val = str(blob.get(key) or "").strip()
            if val and len(val) >= 2 and not is_bad_display_name(val):
                return True
    return False


def extract_buyer_name_from_user_card(inner: dict[str, Any] | None, *, uid: str = "") -> str:
    if not user_card_has_buyer_nick(inner if isinstance(inner, dict) else {}):
        return ""
    name = extract_buyer_name_from_obj(inner if isinstance(inner, dict) else {})
    if name and not is_bad_display_name(name, uid=uid):
        return name
    return ""


def prune_untrusted_buyer_display_names(
    session,
    *,
    save: bool = True,
    verify_user_card: bool = True,
) -> int:
    extra = getattr(session, "extra", None)
    if not isinstance(extra, dict):
        return 0
    raw = extra.get("buyer_display_names")
    if not isinstance(raw, dict) or not raw:
        return 0

    mp = dict(raw)
    removed = 0
    for uid, name in list(mp.items()):
        u = str(uid or "").strip()
        n = str(name or "").strip()
        if not u or not n:
            mp.pop(uid, None)
            removed += 1
            continue
        if is_bad_display_name(n, uid=u) or is_uid_fallback_label(n, u):
            mp.pop(uid, None)
            removed += 1
            continue
        if not verify_user_card:
            continue

        from pigeon_protocol.conv_list_fallback import _user_card_hint

        hint = _user_card_hint(session, u)
        card = hint.get("card") if isinstance(hint.get("card"), dict) else {}
        card_name = str(hint.get("name") or "").strip()
        if card:
            if not user_card_has_buyer_nick(card):
                mp.pop(uid, None)
                removed += 1
                continue
            verified = extract_buyer_name_from_user_card(card, uid=u)
            if verified and verified != n:
                mp[u] = verified
            elif not verified:
                mp.pop(uid, None)
                removed += 1
        elif not card_name:
            mp.pop(uid, None)
            removed += 1
    if not verify_user_card:
        by_name: dict[str, list[str]] = {}
        for uid, name in mp.items():
            by_name.setdefault(str(name), []).append(str(uid))
        for _name, uid_list in by_name.items():
            if len(uid_list) <= 1:
                continue
            for uid in uid_list[1:]:
                mp.pop(uid, None)
                removed += 1
    if removed or mp != raw:
        extra["buyer_display_names"] = mp
        session.extra = extra
        if save:
            try:
                from pigeon_protocol.session import save_session

                save_session(session)
            except OSError as exc:
                logger.debug("prune buyer_display_names save: %s", exc)
    return removed


def is_bad_display_name(name: str, *, uid: str = "") -> bool:
    n = str(name or "").strip()
    if not n:
        return True
    if is_uid_fallback_label(n, uid):
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


def _decode_json_string(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return str(json.loads(f'"{text}"'))
    except Exception:
        return text


def _protobuf_blob(data: bytes | str) -> str:
    if isinstance(data, str):
        return data
    text = data.decode("utf-8", errors="ignore")
    if "uname" in text or "nickname" in text or "AQ" in text:
        return text
    return data.decode("latin-1", errors="ignore")


def _nick_priority(window: str) -> int:
    if "s:sender_biz_role" in window and "Buyer" in window:
        return 1
    if "Buyer" in window or "sender_role\x12\x011" in window or '"direction":1' in window:
        return 2
    if "CurrentServer" in window or "sender_role\x12\x012" in window or '"biz_role_type":"cs"' in window:
        return 8
    return 5


def _clean_proto_nick(raw: str) -> str:
    name = _decode_json_string(raw).strip()
    name = re.sub(r"[\x00-\x1fJ].*$", "", name).strip()
    m = re.match(r"([\u4e00-\u9fff][\u4e00-\u9fff\w\-.·]{0,38})", name)
    if m:
        name = m.group(1).rstrip("Pp")
    return name


def extract_buyer_nickname_from_protobuf(data: bytes | str) -> str:
    """Scan pigeon_im / WS protobuf for buyer nickname (biz_ext.nickname / uname)."""
    blob = _protobuf_blob(data)
    if not blob:
        return ""
    candidates: list[tuple[int, str]] = []

    def add(priority: int, raw: str) -> None:
        name = _clean_proto_nick(raw)
        if name and not is_bad_display_name(name):
            candidates.append((priority, name))

    for m in re.finditer(r'"(?:nickname|uname)"\s*:\s*"((?:\\.|[^"\\]){1,80})"', blob):
        window = blob[max(0, m.start() - 500) : m.end() + 200]
        add(_nick_priority(window), m.group(1))

    for m in re.finditer(
        r"(?:uname|nickname)[\x00-\x1f]{1,4}([\u4e00-\u9fff][\u4e00-\u9fff\w\-.]{0,38})",
        blob,
    ):
        window = blob[max(0, m.start() - 500) : m.end() + 200]
        add(_nick_priority(window), m.group(1))

    if not candidates:
        return ""
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def extract_buyer_nickname_for_uid(data: bytes | str, uid: str) -> str:
    """Extract buyer nick near a security_user_id in pigeon_im protobuf / HAR capture text."""
    blob = _protobuf_blob(data)
    u = str(uid or "").strip()
    if not blob:
        return ""
    candidates: list[tuple[int, int, str]] = []
    uid_pos = blob.find(u) if u else -1
    if u and uid_pos < 0:
        return ""
    for m in re.finditer(
        r"(?:uname|nickname)[\x00-\x1f]{1,4}([\u4e00-\u9fff][\u4e00-\u9fff\w\-.]{0,38})",
        blob,
    ):
        name = _clean_proto_nick(m.group(1))
        if not name or is_bad_display_name(name, uid=u):
            continue
        window = blob[max(0, m.start() - 400) : m.end() + 200]
        if u and u not in window:
            continue
        pri = _nick_priority(window)
        dist = abs(m.start() - uid_pos) if uid_pos >= 0 else 0
        candidates.append((pri, dist, name))
    for m in re.finditer(r'"(?:nickname|uname)"\s*:\s*"((?:\\.|[^"\\]){1,80})"', blob):
        if u and u not in blob[max(0, m.start() - 800) : m.end() + 200]:
            continue
        name = _clean_proto_nick(m.group(1))
        if not name or is_bad_display_name(name, uid=u):
            continue
        window = blob[max(0, m.start() - 400) : m.end() + 200]
        candidates.append((_nick_priority(window), 0, name))
    if not candidates:
        return extract_buyer_nickname_from_protobuf(blob) if not u else ""
    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][2]


def load_buyer_names_from_captures(uids: list[str], *, session=None) -> dict[str, str]:
    """Offline uid→nick from local HAR captures (IM history, user_card, init sync)."""
    from urllib.parse import parse_qs, urlparse

    from pigeon_protocol.capture_loader import index_captures, load_capture

    targets = {str(u or "").strip() for u in uids if str(u or "").strip()}
    found: dict[str, str] = {}
    if not targets:
        return found

    def _remember(uid: str, name: str) -> None:
        if not uid or not name:
            return
        found[uid] = name
        targets.discard(uid)

    for path in index_captures().http_bodies:
        try:
            ev = load_capture(path)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        url = str(ev.get("url") or "")
        body = str(ev.get("response_body") or "")
        if not body:
            continue

        if "get_user_card" in url:
            qs = parse_qs(urlparse(url).query)
            uid = str((qs.get("security_user_id") or [""])[0]).strip()
            if uid not in targets:
                continue
            try:
                doc = json.loads(body)
            except json.JSONDecodeError:
                continue
            data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
            inner = data.get("data") if isinstance(data.get("data"), dict) else data
            name = extract_buyer_name_from_user_card(inner if isinstance(inner, dict) else {}, uid=uid)
            if name and not is_bad_display_name(name, uid=uid):
                _remember(uid, name)
            continue

        for uid in list(targets):
            if uid not in body:
                continue
            name = extract_buyer_nickname_for_uid(body, uid)
            if name and not is_bad_display_name(name, uid=uid):
                _remember(uid, name)
        if not targets:
            break

    if session is not None and found:
        for uid, name in found.items():
            remember_buyer_display_name(session, uid, name, save=False)
        try:
            from pigeon_protocol.session import save_session

            save_session(session)
        except OSError as exc:
            logger.debug("save capture buyer names: %s", exc)
    return found


def get_buyer_display_names(session) -> dict[str, str]:
    extra = getattr(session, "extra", None) or {}
    raw = extra.get("buyer_display_names") if isinstance(extra, dict) else {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for uid, name in raw.items():
        u = str(uid or "").strip()
        n = str(name or "").strip()
        if u and n and not is_bad_display_name(n, uid=u):
            out[u] = n
    return out


def remember_buyer_display_name(session, uid: str, name: str, *, save: bool = True) -> bool:
    u = str(uid or "").strip()
    n = str(name or "").strip()
    if not u or not n or is_bad_display_name(n, uid=u):
        return False
    from pigeon_protocol.session import save_session

    extra = getattr(session, "extra", None)
    if not isinstance(extra, dict):
        extra = {}
        session.extra = extra
    mp = dict(get_buyer_display_names(session))
    if mp.get(u) == n:
        return False
    mp[u] = n
    extra["buyer_display_names"] = mp
    if save:
        try:
            save_session(session)
        except OSError as exc:
            logger.debug("save buyer_display_names: %s", exc)
    return True


def hint_buyer_name_from_context(session, uid: str) -> str:
    cached = get_buyer_display_names(session).get(uid, "")
    if cached:
        return cached
    try:
        from pigeon_protocol.pigeon_im import fetch_context_pure

        ctx = fetch_context_pure(session, uid, shop_id=str(getattr(session, "shop_id", "") or ""))
        name = str(getattr(ctx, "buyer_name", "") or "").strip()
        if not name:
            for msg in ctx.messages or []:
                if str(msg.get("role") or "") not in ("buyer", "customer"):
                    continue
                nick = str(msg.get("nickname") or "").strip()
                if nick and not is_bad_display_name(nick, uid=uid):
                    name = nick
                    break
        if name and remember_buyer_display_name(session, uid, name, save=True):
            return name
        if name:
            return name
    except Exception as exc:
        logger.debug("hint_buyer_name_from_context %s: %s", uid[-6:], exc)
    return ""


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
    if is_bad_display_name(name, uid=uid):
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


def _apply_display_name(item: dict[str, Any], name: str, source: str) -> None:
    item["name"] = name
    item["buyer_name"] = name
    item["display_name"] = name
    item["name_source"] = source


def enrich_items_light(
    session,
    items: list[dict[str, Any]],
    *,
    context_limit: int = 10,
    user_card_limit: int = 8,
) -> list[dict[str, Any]]:
    """Fast conv-list enrich: session cache + local HAR captures (+ optional pigeon_im)."""
    pending: list[str] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        uid = str(raw.get("security_user_id") or raw.get("security_uid") or "")
        current = str(raw.get("display_name") or raw.get("buyer_name") or raw.get("name") or "")
        if uid and (
            is_uid_fallback_label(current, uid)
            or is_bad_display_name(current, uid=uid)
        ):
            pending.append(uid)
    capture_names = load_buyer_names_from_captures(pending, session=session)
    cached = get_buyer_display_names(session)
    out: list[dict[str, Any]] = []
    context_tried = 0
    card_tried = 0
    dirty = bool(capture_names)
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        uid = str(item.get("security_user_id") or item.get("security_uid") or "")
        current = str(item.get("display_name") or item.get("buyer_name") or item.get("name") or "")
        if uid and cached.get(uid) and (
            is_uid_fallback_label(current, uid) or is_bad_display_name(current, uid=uid)
        ):
            _apply_display_name(item, cached[uid], "session_cache")
        current = str(item.get("display_name") or item.get("buyer_name") or item.get("name") or "")
        if uid and capture_names.get(uid) and (
            is_uid_fallback_label(current, uid) or is_bad_display_name(current, uid=uid)
        ):
            _apply_display_name(item, capture_names[uid], "capture_nickname")
            remember_buyer_display_name(session, uid, capture_names[uid], save=False)
            dirty = True
        current = str(item.get("display_name") or item.get("buyer_name") or item.get("name") or "")
        if (
            uid
            and card_tried < user_card_limit
            and (is_uid_fallback_label(current, uid) or is_bad_display_name(current, uid=uid))
        ):
            card_tried += 1
            try:
                from pigeon_protocol.conv_list_fallback import _user_card_hint

                hint = _user_card_hint(session, uid)
                card_name = str(hint.get("name") or "").strip()
                if card_name and not is_bad_display_name(card_name, uid=uid):
                    _apply_display_name(item, card_name, "user_card")
                    remember_buyer_display_name(session, uid, card_name, save=False)
                    dirty = True
            except Exception as exc:
                logger.debug("light user_card %s: %s", uid[-6:], exc)
        current = str(item.get("display_name") or item.get("buyer_name") or item.get("name") or "")
        if (
            uid
            and context_tried < max(context_limit, len(pending))
            and (is_uid_fallback_label(current, uid) or is_bad_display_name(current, uid=uid))
        ):
            context_tried += 1
            ctx_name = hint_buyer_name_from_context(session, uid)
            if ctx_name:
                _apply_display_name(item, ctx_name, "context_nickname")
                dirty = True
        out.append(normalize_conversation_item(item))
    if dirty or context_tried or card_tried:
        try:
            from pigeon_protocol.session import save_session

            save_session(session)
        except OSError as exc:
            logger.debug("save light enrich names: %s", exc)
    return out


def enrich_items_with_user_card(
    session,
    items: list[dict[str, Any]],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    from pigeon_protocol.conv_list_fallback import _user_card_hint

    cached = get_buyer_display_names(session)
    out: list[dict[str, Any]] = []
    enriched = 0
    context_tried = 0
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        uid = str(item.get("security_user_id") or item.get("security_uid") or "")
        current = str(item.get("display_name") or item.get("buyer_name") or item.get("name") or "")
        if uid and cached.get(uid) and is_bad_display_name(current, uid=uid):
            _apply_display_name(item, cached[uid], "session_cache")
        current = str(item.get("display_name") or item.get("buyer_name") or item.get("name") or "")
        uid_label = buyer_label_from_uid(uid) if uid else ""
        needs_lookup = bool(uid) and (
            not current
            or is_bad_display_name(current, uid=uid)
            or is_uid_fallback_label(current, uid)
            or current.replace(" ", "") == uid_label.replace(" ", "")
        )
        if needs_lookup and enriched < limit:
            hint = _user_card_hint(session, uid)
            card_name = str(hint.get("name") or "").strip()
            if card_name and not is_bad_display_name(card_name, uid=uid):
                _apply_display_name(item, card_name, "user_card")
                remember_buyer_display_name(session, uid, card_name, save=False)
                if hint.get("buyer_source"):
                    item["buyer_source"] = str(hint.get("buyer_source") or "")
                if isinstance(hint.get("card"), dict):
                    item["card"] = hint["card"]
                enriched += 1
        current = str(item.get("display_name") or item.get("buyer_name") or item.get("name") or "")
        if (
            uid
            and context_tried < 5
            and (is_uid_fallback_label(current, uid) or is_bad_display_name(current, uid=uid))
        ):
            context_tried += 1
            ctx_name = hint_buyer_name_from_context(session, uid)
            if ctx_name:
                _apply_display_name(item, ctx_name, "context_nickname")
        out.append(normalize_conversation_item(item))
    if enriched or context_tried:
        try:
            from pigeon_protocol.session import save_session

            if callable(getattr(session, "to_dict", None)):
                save_session(session)
        except (OSError, AttributeError, TypeError):
            pass
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
