"""Import live WS send captures into template pool."""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any

from pigeon_protocol.config import LIVE_CAPTURES

CAP_DIR = LIVE_CAPTURES / "ws_sign"
SAMPLES_FILE = Path(__file__).resolve().parents[2] / "analysis" / "ws_sign_samples.json"


def _sample_text(raw: bytes) -> tuple[str, int]:
    import re

    from pigeon_protocol.parsers.ws_frame_builder import WSFrameBuilder

    try:
        text = WSFrameBuilder(raw)._extract_template_text()
        if text:
            return text[:40], len(text.encode("utf-8"))
    except Exception:
        pass
    hits = re.findall(r"[\u4e00-\u9fff]{1,40}", raw.decode("utf-8", errors="ignore"))
    for h in hits:
        if 1 <= len(h.encode("utf-8")) <= 120:
            return h, len(h.encode("utf-8"))
    return "", 0


def _safe_name(text: str, byte_len: int, frame_len: int) -> str:
    if text:
        safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", text)[:24]
        if safe:
            return safe
    return f"len{byte_len or frame_len}"


def sample_to_event(sample: dict[str, Any]) -> dict[str, Any]:
    raw = base64.b64decode(str(sample.get("b64") or sample.get("payload") or ""))
    text, byte_len = _sample_text(raw)
    return {
        "type": "ws_frame_sent",
        "source": sample.get("source") or "cdp_active_ws_capture",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(sample.get("t", 0) / 1000)),
        "url": str(sample.get("url") or ""),
        "payload": base64.b64encode(raw).decode("ascii"),
        "payload_length": sample.get("len") or len(raw),
        "text_hint": [text] if text else [],
        "text_byte_length": byte_len,
    }


def import_sample(sample: dict[str, Any], *, cap_dir: Path | None = None) -> Path:
    cap_dir = cap_dir or CAP_DIR
    cap_dir.mkdir(parents=True, exist_ok=True)
    event = sample_to_event(sample)
    raw = base64.b64decode(str(event["payload"]))
    text, byte_len = _sample_text(raw)
    name = _safe_name(text, byte_len, len(raw))
    out = cap_dir / f"live_ws_frame_sent_{name}.json"
    out.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def import_samples_file(path: Path | None = None) -> list[Path]:
    src = path or SAMPLES_FILE
    if not src.exists():
        return []
    data = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    saved: list[Path] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            saved.append(import_sample(item))
        except Exception:
            continue
    return saved
