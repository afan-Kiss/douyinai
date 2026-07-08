"""Ingest live 169B inners from CDP browser send captures into session cache."""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("pigeon.ws_cdp_inner_ingest")

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HOOK = ROOT / "analysis" / "pigeon_rust_hook.json"


def _decode_frame_inner(b64: str) -> dict[str, Any] | None:
    from pigeon_protocol.parsers.ws_frame_builder import WSFrameBuilder
    from pigeon_protocol.ws_sign import locate_signature_region
    from pigeon_protocol.ws_sign_decode import analyze_frame, decode_blob, guess_inner_layout

    try:
        raw = base64.b64decode(b64)
    except Exception:
        return None
    region = locate_signature_region(raw)
    if not region:
        return None
    try:
        inner = decode_blob(region.blob)
    except Exception:
        return None
    if len(inner) != 169:
        return None
    text = WSFrameBuilder(raw)._extract_template_text()
    text_b = len(text.encode("utf-8"))
    layout = guess_inner_layout(inner)
    return {
        "frame_len": len(raw),
        "text": text,
        "text_b": text_b,
        "inner": inner,
        "inner_hex": inner.hex(),
        "layout": layout,
    }


def collect_inners_from_hook(doc: dict[str, Any]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_b64(b64: str, *, source: str) -> None:
        if not b64:
            return
        row = _decode_frame_inner(b64)
        if not row:
            return
        fp = str(row["layout"].get("sha256_prefix") or row["inner_hex"][:16])
        if fp in seen:
            return
        seen.add(fp)
        row["source"] = source
        hits.append(row)

    for row in doc.get("ws_sends") or []:
        if isinstance(row, dict):
            add_b64(str(row.get("b64") or ""), source="ws_send")
    for row in doc.get("new_samples") or []:
        if isinstance(row, dict):
            add_b64(str(row.get("b64") or ""), source="ws_capture")
    for row in doc.get("inners") or []:
        if isinstance(row, dict) and row.get("inner_hex"):
            hx = str(row["inner_hex"])
            if hx[:16] in seen:
                continue
            try:
                inner = bytes.fromhex(hx)
            except ValueError:
                continue
            if len(inner) != 169:
                continue
            from pigeon_protocol.ws_sign_decode import guess_inner_layout

            seen.add(hx[:16])
            hits.append(
                {
                    "frame_len": row.get("frame_len"),
                    "text": "",
                    "text_b": 0,
                    "inner": inner,
                    "inner_hex": hx,
                    "layout": row.get("layout") or guess_inner_layout(inner),
                    "source": "decoded",
                }
            )
    return hits


def ingest_hook_file(session, path: Path | None = None, *, persist: bool = True) -> dict[str, Any]:
    """Load pigeon_rust_hook.json and store inners mapped by textB class."""
    from pigeon_protocol.foundation.ws_blob_compute import (
        _store_session_class_inner,
        inner_class_for_text_b,
    )

    hook_path = path or DEFAULT_HOOK
    if not hook_path.is_file():
        return {"ok": False, "error": f"missing {hook_path}"}

    doc = json.loads(hook_path.read_text(encoding="utf-8"))
    hits = collect_inners_from_hook(doc)
    applied: list[dict[str, Any]] = []

    for hit in sorted(hits, key=lambda h: int(h.get("text_b") or 0), reverse=True):
        inner = hit["inner"]
        text_b = int(hit.get("text_b") or 0)
        if text_b <= 0:
            continue
        ic = inner_class_for_text_b(text_b)
        if not ic:
            continue
        class_id = ic.class_id
        class_name = ic.name

        _store_session_class_inner(session, class_id, inner)
        applied.append(
            {
                "class": class_name,
                "class_id": class_id[:16],
                "text_b": text_b,
                "header_hex": inner[:8].hex(),
                "source": hit.get("source"),
            }
        )
        logger.info(
            "cdp inner class=%s textB=%s header=%s",
            class_name,
            text_b,
            inner[:8].hex(),
        )

    if persist and applied:
        from pigeon_protocol.session import save_session

        try:
            save_session(session)
        except Exception as exc:
            logger.debug("save_session after cdp ingest: %s", exc)

    return {
        "ok": bool(applied),
        "hits": len(hits),
        "applied": applied,
        "hook_ok": doc.get("ok"),
    }


def refresh_inners_via_cdp(session, *, warm_all: bool = True) -> dict[str, Any]:
    """
    Capture fresh session-scoped 169B inners via CDP (requires Chrome :9222 + Feige chat).

    warm_all: send one message per A–G class (7 sends). Otherwise single short send.
    """
    import subprocess
    import sys

    from pigeon_protocol.cdp_bridge import cdp_ready
    from pigeon_protocol.foundation.ws_blob_compute import _load_session_class_cache, inner_class_registry

    if not cdp_ready():
        return {"ok": False, "skipped": "cdp not ready"}

    cached = _load_session_class_cache(session)
    need = {ic.class_id for ic in inner_class_registry().values()}
    have = {k for k in cached if not k.startswith("_") and k != "__init_sync__"}

    if warm_all and len(have & need) < 7:
        warm_script = ROOT / "scripts" / "cdp_warm_session_inners.py"
        if warm_script.is_file():
            proc = subprocess.run(
                [sys.executable, str(warm_script)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=360,
            )
            out_path = ROOT / "analysis" / "cdp_session_inners.json"
            if out_path.is_file():
                try:
                    doc = json.loads(out_path.read_text(encoding="utf-8"))
                    return {
                        "ok": bool(doc.get("ok")),
                        "mode": "warm_all",
                        "stored": doc.get("stored"),
                        "exit": proc.returncode,
                    }
                except json.JSONDecodeError:
                    pass
            return {
                "ok": proc.returncode == 0,
                "mode": "warm_all",
                "exit": proc.returncode,
                "stderr": (proc.stderr or "")[:400],
            }

    script = ROOT / "scripts" / "cdp_hook_pigeon_rust.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    report = ingest_hook_file(session, DEFAULT_HOOK, persist=True)
    report["mode"] = "single_send"
    report["hook_exit"] = proc.returncode
    if proc.returncode != 0 and not report.get("applied"):
        report["stderr"] = (proc.stderr or proc.stdout or "")[:400]
    report["ok"] = bool(report.get("applied"))
    return report
