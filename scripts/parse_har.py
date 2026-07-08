#!/usr/bin/env python3
"""Parse Chrome HAR export for pigeon protocol session + captures."""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pigeon_protocol.session import SessionState, save_session, extract_session_from_capture_event


INTERESTING_URL_PARTS = (
    "order/query",
    "get_history_msg",
    "get_by_conversation",
    "get_user_message",
    "get_message_by_init",
    "fuzzySearchConversation",
    "get_user_card",
    "get_consulting_products",
    "msg_body",
    "pigeon_im/v1/message",
    "ws/v2",
    "frontier.snssdk.com",
)


def load_har(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def cookie_list_to_dict(cookies: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in cookies:
        name = str(c.get("name") or "").strip()
        value = str(c.get("value") or "").strip()
        if name and value:
            out[name] = value
    return out


def header_list_to_dict(headers: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in headers:
        name = str(h.get("name") or "").strip()
        value = str(h.get("value") or "").strip()
        if name:
            out[name] = value
    return out


def is_interesting_url(url: str) -> bool:
    return any(p in url for p in INTERESTING_URL_PARTS)


def decode_response_body(content: dict[str, Any]) -> str:
    body_text = str(content.get("text") or "")
    if content.get("encoding") == "base64" and body_text:
        try:
            return base64.b64decode(body_text).decode("utf-8", errors="replace")
        except Exception:
            pass
    return body_text


def har_entry_to_http_capture(entry: dict[str, Any], idx: int) -> dict[str, Any] | None:
    req = entry.get("request") or {}
    resp = entry.get("response") or {}
    url = str(req.get("url") or "")
    method = str(req.get("method") or "GET").upper()
    if not url or url.startswith("wss://"):
        return None
    if method in {"OPTIONS", "HEAD"}:
        return None
    if not is_interesting_url(url) and "pigeon.jinritemai.com/backstage" not in url and "pigeon_im/v1/message" not in url:
        return None

    headers = header_list_to_dict(req.get("headers") or [])
    content = resp.get("content") or {}
    body_text = decode_response_body(content)

    post_data = req.get("postData") or {}
    post_text = post_data.get("text") or ""

    ts = entry.get("startedDateTime") or datetime.now(timezone.utc).isoformat()
    safe_ts = re.sub(r"[^0-9]", "", ts)[:17] or str(idx)

    return {
        "ts": ts,
        "type": "http_body",
        "request_id": f"har_{idx}",
        "url": url,
        "method": req.get("method") or "GET",
        "headers": headers,
        "post_data": post_text or None,
        "response_body": body_text,
        "_har_index": idx,
    }


def har_ws_messages_to_captures(entry: dict[str, Any], idx: int) -> list[dict[str, Any]]:
    req = entry.get("request") or {}
    url = str(req.get("url") or "")
    if not url.startswith("wss://"):
        return []

    out: list[dict[str, Any]] = []
    ts_base = entry.get("startedDateTime") or datetime.now(timezone.utc).isoformat()

    out.append(
        {
            "ts": ts_base,
            "type": "ws_created",
            "request_id": f"har_ws_{idx}",
            "url": url,
        }
    )

    for mi, msg in enumerate(entry.get("_webSocketMessages") or []):
        direction = str(msg.get("type") or "").lower()
        if direction not in {"send", "receive"}:
            continue
        data = msg.get("data") or ""
        opcode = msg.get("opcode", 1)
        if opcode == 2 or (isinstance(data, str) and not data.startswith("{")):
            fmt = "binary"
            payload_b64 = ""
            payload_hex = ""
            if isinstance(data, str):
                try:
                    raw = base64.b64decode(data)
                    payload_b64 = data
                    payload_hex = raw.hex()
                except Exception:
                    payload_hex = data.encode("latin1", errors="ignore").hex()
            else:
                raw = bytes(data) if isinstance(data, (bytes, bytearray)) else b""
                payload_b64 = base64.b64encode(raw).decode("ascii")
                payload_hex = raw.hex()
        else:
            fmt = "text"
            payload_b64 = str(data)
            payload_hex = str(data).encode("utf-8").hex()

        typ = "ws_frame_sent" if direction == "send" else "ws_frame_received"
        out.append(
            {
                "ts": ts_base,
                "type": typ,
                "direction": "out" if direction == "send" else "in",
                "request_id": f"har_ws_{idx}_{mi}",
                "url": url,
                "opcode": opcode,
                "format": fmt,
                "payload": payload_b64,
                "payload_hex": payload_hex,
                "payload_length": len(bytes.fromhex(payload_hex)) if payload_hex else 0,
            }
        )
    return out


def build_session_from_har(har: dict[str, Any]) -> SessionState:
    session = SessionState()
    entries = (har.get("log") or {}).get("entries") or []

    # Global cookies from HAR log.cookies if present
    log_cookies = (har.get("log") or {}).get("cookies") or []
    session.cookies.update(cookie_list_to_dict(log_cookies))

    for idx, entry in enumerate(entries):
        req = entry.get("request") or {}
        url = str(req.get("url") or "")

        # Per-request cookies
        req_cookies = cookie_list_to_dict(req.get("cookies") or [])
        session.cookies.update(req_cookies)

        headers = header_list_to_dict(req.get("headers") or [])
        if headers.get("Cookie") and not session.cookies:
            for part in headers["Cookie"].split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    session.cookies[k.strip()] = v.strip()

        for key in ("User-Agent", "x-secsdk-csrf-token", "Referer", "Origin"):
            if headers.get(key):
                session.headers[key] = headers[key]

        event = {"url": url, "headers": headers}
        if req.get("postData", {}).get("text"):
            event["post_data"] = req["postData"]["text"]
        extract_session_from_capture_event(session, event, f"har_entry_{idx}")

        if url.startswith("wss://") and url not in session.ws_urls:
            session.ws_urls.append(url)

    session.notes.append(f"extracted from HAR with {len(entries)} entries")
    return session


def parse_har(path: Path, out_dir: Path) -> dict[str, Any]:
    har = load_har(path)
    entries = (har.get("log") or {}).get("entries") or []
    out_dir.mkdir(parents=True, exist_ok=True)

    http_saved = 0
    ws_saved = 0
    url_counter: Counter[str] = Counter()
    captures: list[Path] = []

    for idx, entry in enumerate(entries):
        req = entry.get("request") or {}
        url = str(req.get("url") or "")
        if url:
            path_part = urlparse(url).path or url.split("?")[0]
            if "jinritemai" in url or "snssdk" in url:
                url_counter[path_part.split("/")[-1][:40] or path_part[-40:]] += 1

        http_cap = har_entry_to_http_capture(entry, idx)
        if http_cap:
            name = f"har_{idx:05d}_http_body.json"
            p = out_dir / name
            p.write_text(json.dumps(http_cap, ensure_ascii=False, indent=2), encoding="utf-8")
            http_saved += 1
            captures.append(p)

        for ws_cap in har_ws_messages_to_captures(entry, idx):
            typ = ws_cap.get("type", "ws")
            mi = ws_cap.get("request_id", "").split("_")[-1]
            name = f"har_{idx:05d}_{typ}_{mi}.json"
            p = out_dir / name
            p.write_text(json.dumps(ws_cap, ensure_ascii=False, indent=2), encoding="utf-8")
            ws_saved += 1
            captures.append(p)

    session = build_session_from_har(har)
    session_path = save_session(session, ROOT / "session" / "session.json")

    summary = {
        "har_file": str(path),
        "entries": len(entries),
        "http_captures_saved": http_saved,
        "ws_captures_saved": ws_saved,
        "session_file": str(session_path),
        "cookies": len(session.cookies),
        "cookie_keys": sorted(session.cookies.keys())[:30],
        "query_tokens": list(session.query_tokens.keys()),
        "ws_urls": len(session.ws_urls),
        "top_url_suffixes": url_counter.most_common(25),
    }
    (out_dir / "_har_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("har", type=Path, help="path to .har file")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "captures" / "live" / "from_har",
        help="output capture directory",
    )
    args = parser.parse_args()
    if not args.har.exists():
        print(f"not found: {args.har}", file=sys.stderr)
        return 1
    summary = parse_har(args.har, args.out)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
