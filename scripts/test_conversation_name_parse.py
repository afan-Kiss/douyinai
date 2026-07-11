#!/usr/bin/env python3
"""Unit tests for buyer display name parsing in conversation lists."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.buyer_display_name import (
    buyer_label_from_uid,
    enrich_items_with_user_card,
    extract_buyer_name_from_obj,
    extract_conversation_display_name,
    is_bad_display_name,
    normalize_conversation_item,
    sanitize_conv_preview,
)
from pigeon_protocol.conv_list import parse_conversation_items


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_bad_names() -> None:
    for bad in ("其他", "站内push推送", "未知", "fallback_x", "12"):
        _assert(is_bad_display_name(bad), f"expected bad: {bad}")
    _assert(not is_bad_display_name("小王"), "小王 should be valid")
    _assert(not is_bad_display_name("珠宝客户A"), "珠宝客户A should be valid")


def test_fallback_user_from_desc_only() -> None:
    inner = {"user_from_desc": "其他"}
    name = extract_buyer_name_from_obj(inner)
    _assert(name == "", "user_from_desc must not become name")
    uid = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"
    label = buyer_label_from_uid(uid)
    _assert(label.startswith("买家"), label)
    _assert("其他" not in label, label)


def test_fallback_nested_nick() -> None:
    inner = {
        "user_info": {"nick_name": "小王"},
        "user_from_desc": "其他",
    }
    name = extract_buyer_name_from_obj(inner)
    _assert(name == "小王", f"expected 小王 got {name!r}")


def test_xundan_title_vs_nickname() -> None:
    it = {
        "title": "站内push推送",
        "user_info": {"nickname": "珠宝客户A"},
        "security_user_id": "AQTest12345678901234567890123456789012345678901234567890123456789012",
    }
    name, src = extract_conversation_display_name(it, {}, {})
    _assert(name == "珠宝客户A", f"expected 珠宝客户A got {name!r} via {src}")


def test_xundan_title_other_fallback() -> None:
    uid = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"
    raw = {
        "data": {
            "data": {
                "user_list": [
                    {
                        "title": "其他",
                        "security_user_id": uid,
                    }
                ]
            }
        }
    }
    items = parse_conversation_items(raw)
    _assert(len(items) == 1, "expected one item")
    item = items[0]
    _assert(item["name"] == buyer_label_from_uid(uid), item["name"])
    _assert(item["name"] != "其他", item["name"])
    _assert(item.get("buyer_name") == item["name"], item)
    _assert(item.get("display_name") == item["name"], item)


def test_parse_items_fields() -> None:
    uid = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"
    raw = {
        "items": [
            {
                "nick_name": "李女士",
                "security_user_id": uid,
                "preview": "你好",
            }
        ]
    }
    items = parse_conversation_items(raw)
    _assert(items[0]["name"] == "李女士", items[0]["name"])
    _assert(items[0]["buyer_name"] == "李女士", items[0])
    _assert(items[0]["display_name"] == "李女士", items[0])
    _assert(items[0]["name"] != "其他", items[0]["name"])


def test_normalize_cached_bad_name() -> None:
    uid = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"
    item = {
        "security_user_id": uid,
        "name": "其他",
        "display_name": "其他",
        "buyer_name": "其他",
        "preview": "已知买家（xundan 11001 fallback）",
        "buyer_source": "其他",
    }
    out = normalize_conversation_item(item)
    _assert(out["name"] == buyer_label_from_uid(uid), out["name"])
    _assert(out["name"] != "其他", out["name"])
    _assert(out["preview"] == "已知买家", out["preview"])


def test_normalize_nested_card_name() -> None:
    uid = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"
    item = {
        "security_user_id": uid,
        "name": "站内push推送",
        "card": {
            "user_from_desc": "其他",
            "user_info": {"nick_name": "珠宝客户A"},
        },
    }
    out = normalize_conversation_item(item)
    _assert(out["name"] == "珠宝客户A", out["name"])


def test_enrich_user_card(monkeypatch=None) -> None:
    uid = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"
    items = [{"security_user_id": uid, "name": "其他", "display_name": "其他"}]

    class _Session:
        extra: dict = {}

        def to_dict(self):
            return {"extra": self.extra}

    def _fake_hint(_session, _uid: str) -> dict:
        return {
            "name": "小王",
            "buyer_source": "其他",
            "preview": "成交3单",
            "card": {"user_info": {"nick_name": "小王"}},
        }

    import pigeon_protocol.conv_list_fallback as fb

    old = fb._user_card_hint
    fb._user_card_hint = _fake_hint
    try:
        out = enrich_items_with_user_card(_Session(), items)
        _assert(out[0]["name"] == "小王", out[0]["name"])
        _assert(out[0]["name_source"] in ("user_card", "card"), out[0]["name_source"])
    finally:
        fb._user_card_hint = old


def test_preview_sanitize() -> None:
    p = sanitize_conv_preview("已知买家（xundan 11001 fallback）")
    _assert(p == "已知买家", p)


def test_uid_fallback_label() -> None:
    from pigeon_protocol.buyer_display_name import is_uid_fallback_label

    uid = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"
    _assert(is_uid_fallback_label("买家3xb6Nk", uid), "uid tail fallback should be bad")
    _assert(is_bad_display_name("买家3xb6Nk", uid=uid), "uid tail fallback via is_bad_display_name")
    _assert(not is_bad_display_name("一一潮玩", uid=uid), "real nickname should stay")


def test_har_capture_buyer_nick() -> None:
    from pathlib import Path

    from pigeon_protocol.buyer_display_name import extract_buyer_nickname_for_uid, load_buyer_names_from_captures

    uid = "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"
    root = Path(__file__).resolve().parents[1]
    har = root / "captures/live/from_har/har_00318_http_body.json"
    if not har.is_file():
        return
    import json

    ev = json.loads(har.read_text(encoding="utf-8"))
    body = str(ev.get("response_body") or "")
    name = extract_buyer_nickname_for_uid(body, uid)
    _assert(name == "一只小青蛙", f"expected 一只小青蛙 got {name!r}")
    found = load_buyer_names_from_captures([uid])
    _assert(found.get(uid) == "一只小青蛙", found)


def main() -> int:
    tests = [
        test_bad_names,
        test_uid_fallback_label,
        test_har_capture_buyer_nick,
        test_fallback_user_from_desc_only,
        test_fallback_nested_nick,
        test_xundan_title_vs_nickname,
        test_xundan_title_other_fallback,
        test_parse_items_fields,
        test_normalize_cached_bad_name,
        test_normalize_nested_card_name,
        test_enrich_user_card,
        test_preview_sanitize,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    if failed:
        print(f"\n{failed} failed")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
