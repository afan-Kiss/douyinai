from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pigeon_protocol.config import AppConfig, REFERENCE_CAPTURES, LIVE_CAPTURES
from pigeon_protocol.session import SessionState, extract_session_from_capture_event


@dataclass
class SendTemplateInfo:
    path: Path
    frame_length: int
    text_byte_length: int
    sample_text: str
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path.name),
            "frame_length": self.frame_length,
            "text_byte_length": self.text_byte_length,
            "sample_text": self.sample_text,
            "source": self.source,
        }


def _template_text_byte_length(raw: bytes) -> tuple[int, str]:
    try:
        from pigeon_protocol.parsers.ws_frame_builder import WSFrameBuilder

        text = WSFrameBuilder(raw)._extract_template_text()
        return len(text.encode("utf-8")), text[:40]
    except Exception:
        return -1, ""


def _score_send_template(path: Path, event: dict[str, Any], raw: bytes, raw_len: int) -> int:
    payload = str(event.get("payload") or "")
    payload_hex = str(event.get("payload_hex") or "")
    decoded_text = raw.decode("utf-8", errors="ignore") if raw else ""
    score = raw_len
    if "type" in payload.lower() or "636c69656e74" in payload_hex:
        score += 5000
    if raw_len >= 2500:
        score += 3000
    if "request_log" in payload and "feat/" in payload and "s:client_message_id" not in payload:
        score -= 8000
    if "from_har" in str(path):
        score += 1500
    if "ws_sign" in str(path) or event.get("source") in (
        "cdp_active_ws_capture",
        "playwright_ws_event",
        "auto_harvest",
    ):
        score += 50000
    if re.search(r"live_ws_frame_sent_b\d{3}\.json$", path.name):
        score += 80000
    if "收到" in decoded_text or "e694b6e588b0" in payload_hex:
        score += 12000
    if "你好" in decoded_text or "嗯嗯" in decoded_text or "好的" in decoded_text:
        score += 15000
    if raw_len >= 3100 and raw_len <= 3250:
        score += 3000
        if 3000 <= raw_len <= 3150:
            score += 8000
        if 3190 <= raw_len <= 3210:
            score += 12000
    if "har_00047_ws_frame_sent_26" in path.name:
        score += 5000
    return score


def index_send_templates(dirs: Iterable[Path] | None = None) -> dict[int, SendTemplateInfo]:
    """Build template pool keyed by UTF-8 text byte length (best capture per length)."""
    idx = index_captures(dirs)
    best_by_len: dict[int, tuple[int, SendTemplateInfo]] = {}

    for path in idx.ws_sent:
        try:
            event = load_capture(path)
        except Exception:
            continue
        payload = str(event.get("payload") or "")
        if not payload:
            continue
        try:
            raw = base64.b64decode(payload)
        except Exception:
            continue
        if len(raw) < 2500:
            continue
        text_len, sample = _template_text_byte_length(raw)
        if text_len <= 0:
            meta = event.get("text_byte_length")
            if isinstance(meta, int) and meta > 0:
                text_len = meta
            else:
                continue
        else:
            meta = event.get("text_byte_length")
            if isinstance(meta, int) and meta > 0 and meta != text_len:
                # Prefer harvest metadata when protobuf nests decoy text fields.
                text_len = meta
                if not sample and event.get("text_hint"):
                    sample = str(event["text_hint"][0])[:40]
        score = _score_send_template(path, event, raw, len(raw))
        info = SendTemplateInfo(
            path=path,
            frame_length=len(raw),
            text_byte_length=text_len,
            sample_text=sample or str(event.get("text_hint", [""])[0] if event.get("text_hint") else ""),
            source=str(event.get("source") or path.parent.name),
        )
        prev = best_by_len.get(text_len)
        if prev is None or score > prev[0]:
            best_by_len[text_len] = (score, info)

    return {length: info for length, (_, info) in best_by_len.items()}


def list_send_template_pool(dirs: Iterable[Path] | None = None) -> list[dict[str, Any]]:
    pool = index_send_templates(dirs)
    return [pool[k].to_dict() for k in sorted(pool.keys())]


@dataclass
class CaptureIndex:
    ws_created: list[Path]
    ws_received: list[Path]
    ws_sent: list[Path]
    http_bodies: list[Path]
    order_requests: list[Path]
    history_requests: list[Path]
    conv_list_requests: list[Path]

    @property
    def total(self) -> int:
        return (
            len(self.ws_created)
            + len(self.ws_received)
            + len(self.ws_sent)
            + len(self.http_bodies)
        )


def iter_capture_files(dirs: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for root in dirs:
        if not root.exists():
            continue
        files.extend(sorted(root.rglob("*.json")))
    return files


def load_capture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _default_capture_roots() -> list[Path]:
    from pigeon_protocol.pure_config import default_capture_dirs

    return default_capture_dirs()


def index_captures(dirs: Iterable[Path] | None = None) -> CaptureIndex:
    roots = list(dirs or _default_capture_roots())
    ws_created: list[Path] = []
    ws_received: list[Path] = []
    ws_sent: list[Path] = []
    http_bodies: list[Path] = []
    order_requests: list[Path] = []
    history_requests: list[Path] = []
    conv_list_requests: list[Path] = []

    for path in iter_capture_files(roots):
        name = path.name
        if "ws_created" in name:
            ws_created.append(path)
        elif "ws_frame_received" in name:
            ws_received.append(path)
        elif "ws_frame_sent" in name or ("ws_sign" in str(path.parent) and "sent" in name):
            ws_sent.append(path)
        elif "http_body" in name or "http_response" in name:
            http_bodies.append(path)
            try:
                event = load_capture(path)
                url = str(event.get("url") or "")
                if "order/query" in url:
                    order_requests.append(path)
                elif "get_history_msg" in url:
                    history_requests.append(path)
                elif "fuzzySearchConversation" in url:
                    conv_list_requests.append(path)
            except Exception:
                pass

    return CaptureIndex(
        ws_created=ws_created,
        ws_received=ws_received,
        ws_sent=ws_sent,
        http_bodies=http_bodies,
        order_requests=order_requests,
        history_requests=history_requests,
        conv_list_requests=conv_list_requests,
    )


def extract_session_from_captures(dirs: Iterable[Path] | None = None) -> SessionState:
    session = SessionState()
    index = index_captures(dirs)
    priority = (
        index.order_requests
        + index.history_requests
        + index.conv_list_requests
        + index.http_bodies[:200]
        + index.ws_created
        + index.ws_sent
        + index.ws_received[:50]
    )
    seen: set[str] = set()
    for path in priority:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            event = load_capture(path)
        except Exception:
            continue
        extract_session_from_capture_event(session, event, key)
    if not session.notes:
        session.notes.append("auto-extracted from captures; verify cookies before live calls")
    return session


def find_send_template(index: CaptureIndex | None = None, *, byte_len: int = 0) -> dict[str, Any] | None:
    if byte_len > 0:
        from pigeon_protocol.ws_sign_bucket import resolve_template_byte_len

        pool = index_send_templates()
        resolved = resolve_template_byte_len(byte_len)
        for try_len in (byte_len, resolved):
            info = pool.get(try_len)
            if info:
                try:
                    return load_capture(info.path)
                except Exception:
                    pass
        # Do not fall through to generic scoring — wrong bucket template breaks send.
        return None

    idx = index or index_captures()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for path in idx.ws_sent:
        try:
            event = load_capture(path)
        except Exception:
            continue
        payload = str(event.get("payload") or "")
        payload_hex = str(event.get("payload_hex") or "")
        blob = payload_hex or payload
        length = int(event.get("payload_length") or len(blob))
        raw_len = length
        decoded_text = ""
        template_text_len = -1
        try:
            if payload:
                raw = base64.b64decode(payload)
                raw_len = len(raw)
                decoded_text = raw.decode("utf-8", errors="ignore")
                template_text_len, _ = _template_text_byte_length(raw)
        except Exception:
            raw = b""
        score = _score_send_template(path, event, raw if payload else b"", raw_len)
        if byte_len > 0 and template_text_len == byte_len:
            score += 100000
        elif byte_len > 0 and template_text_len > 0:
            score -= abs(template_text_len - byte_len) * 500
        candidates.append((score, event))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]
