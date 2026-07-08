#!/usr/bin/env python3
"""Write human-readable HAR analysis report."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from pigeon_protocol.parsers.pigeon_frame_parser import parse_inbound_frame


def main() -> int:
    har_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\1\Desktop\im.jinritemai.com.har")
    out_path = ROOT / "HAR_ANALYSIS.md"
    har = json.loads(har_path.read_text(encoding="utf-8"))
    entries = har["log"]["entries"]

    lines: list[str] = [
        "# HAR 解析报告",
        "",
        f"- 文件: `{har_path}`",
        f"- 请求条目: {len(entries)}",
        "",
    ]

    # WS
    for e in entries:
        url = e["request"]["url"]
        if not url.startswith("wss://ws.fxg"):
            continue
        msgs = e.get("_webSocketMessages") or []
        lines += ["## WebSocket 消息", "", f"- URL: `{url[:120]}...`", f"- 帧数: {len(msgs)}", ""]
        text_rows = []
        for i, m in enumerate(msgs):
            raw = base64.b64decode(m["data"])
            ev = {
                "type": "ws_frame_received" if m["type"] == "receive" else "ws_frame_sent",
                "direction": "in" if m["type"] == "receive" else "out",
                "format": "binary",
                "payload_hex": raw.hex(),
                "url": url,
            }
            frame = parse_inbound_frame(ev)
            text = str(frame.get("text") or "")
            role = str(frame.get("role") or "")
            if text or len(raw) > 2500:
                text_rows.append(f"| {i} | {m['type']} | {role} | {len(raw)} | {text[:60]} |")
        lines += ["| # | 方向 | 角色 | 字节 | 文本 |", "|---|---|---|---:|---|"]
        lines += text_rows[:30]
        lines.append("")

    # Orders
    lines += ["## 订单查询 (order/query)", ""]
    for e in entries:
        if "order/query" not in e["request"]["url"]:
            continue
        rb = e["response"]["content"].get("text", "")
        if e["response"]["content"].get("encoding") == "base64" and rb:
            rb = base64.b64decode(rb).decode("utf-8", errors="replace")
        if not rb.startswith("{"):
            continue
        data = json.loads(rb)
        post = json.loads(e["request"].get("postData", {}).get("text") or "{}")
        total = data.get("total", 0)
        if int(total or 0) <= 0 and not data.get("componentized_data"):
            continue
        lines += [
            f"- security_user_id: `{post.get('security_user_id', '')}`",
            f"- total: **{total}**",
            f"- code: {data.get('code')}",
            "",
        ]
        break

    # Context APIs
    for label, needle in [
        ("get_by_conversation", "get_by_conversation"),
        ("get_user_message", "get_user_message"),
        ("get_message_by_init", "get_message_by_init"),
    ]:
        lines += [f"## {label}", ""]
        for e in entries:
            if needle not in e["request"]["url"]:
                continue
            rb = e["response"]["content"].get("text", "")
            if e["response"]["content"].get("encoding") == "base64" and rb:
                rb = base64.b64decode(rb).decode("utf-8", errors="replace")
            pd = e["request"].get("postData", {}).get("text") or e["request"]["url"]
            lines += [f"- 请求: `{str(pd)[:200]}`", f"- 响应片段: `{rb[:300]}`", ""]
            break

    lines += [
        "## Cookie 情况",
        "",
        "- 此 HAR **未包含 Cookie 头**（Chrome 导出常见情况）",
        "- HTTP live 调用仍需从浏览器 Application/Cookies 补 `session.json`",
        "",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
