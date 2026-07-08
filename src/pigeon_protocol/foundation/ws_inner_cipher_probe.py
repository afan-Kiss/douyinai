"""Probe AES-GCM / ChaCha20 decrypt hypotheses on 161B encrypted inner bodies."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pigeon_protocol.foundation.ws_inner_proto import BODY_LEN, aes_gcm_layout


def _derive_key_candidates(session: Any) -> list[tuple[str, bytes]]:
    cookies = getattr(session, "cookies", None) or {}
    qt = getattr(session, "query_tokens", None) or {}
    extra = getattr(session, "extra", None) or {}

    materials: list[tuple[str, bytes]] = []
    sid = str(cookies.get("sessionid") or "")
    cid = str(cookies.get("PIGEON_CID") or qt.get("device_id") or "")
    ws_token = str(qt.get("token") or "")
    pigeon_sign = str(qt.get("pigeon_sign") or "")

    for label, raw in [
        ("sessionid", sid.encode()),
        ("PIGEON_CID", cid.encode()),
        ("ws_token", ws_token.encode()),
        ("pigeon_sign", pigeon_sign.encode()),
        ("sessionid:PIGEON_CID", f"{sid}:{cid}".encode()),
        ("ws_token+sessionid", (ws_token + sid).encode()),
    ]:
        if not raw or raw == b"":
            continue
        materials.append((label, raw))

    im_token = extra.get("im_access_token") or extra.get("rust_sdk_access_token")
    if im_token:
        materials.append(("im_access_token", str(im_token).encode()))

    # Feige invoke dump if present
    invoke_path = Path(__file__).resolve().parents[3] / "analysis" / "feige_rust_invoke_latest.json"
    if invoke_path.is_file():
        try:
            doc = json.loads(invoke_path.read_text(encoding="utf-8"))
            for step in doc.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                for k in ("access_token_preview", "context_access_token"):
                    v = step.get(k)
                    if v and isinstance(v, str) and len(v) > 8:
                        materials.append((f"invoke_{k}", v.encode()))
        except (OSError, json.JSONDecodeError):
            pass

    keys: list[tuple[str, bytes]] = []
    for label, raw in materials:
        keys.append((f"sha256({label})[:16]", hashlib.sha256(raw).digest()[:16]))
        keys.append((f"sha256({label})", hashlib.sha256(raw).digest()))
        keys.append((f"md5({label})", hashlib.md5(raw).digest()))
    return keys


def probe_body_decrypt(body: bytes, session: Any | None = None) -> dict[str, Any]:
    layout = aes_gcm_layout(body)
    report: dict[str, Any] = {
        "body_len": len(body),
        "layout": layout,
        "hits": [],
        "keys_tried": 0,
    }
    if not layout.get("ok") or not layout.get("fits_ring_aead"):
        report["skipped"] = "body does not match 12+133+16 layout"
        return report

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
    except ImportError:
        report["skipped"] = "cryptography not installed"
        return report

    nonce = body[:12]
    ct_tag = body[12:]
    keys = _derive_key_candidates(session) if session is not None else []
    aads = [b"", body[:8], b"pigeon", b"edbX"]

    for key_label, key in keys:
        for aad in aads:
            report["keys_tried"] += 1
            try:
                pt = AESGCM(key).decrypt(nonce, ct_tag, aad)
                report["hits"].append(
                    {"cipher": "AES-GCM", "key": key_label, "aad": aad.hex() or "empty", "pt_len": len(pt), "pt_head": pt[:32].hex()}
                )
            except Exception:
                pass
            if len(key) == 32:
                try:
                    pt = ChaCha20Poly1305(key).decrypt(nonce, ct_tag, aad)
                    report["hits"].append(
                        {
                            "cipher": "ChaCha20-Poly1305",
                            "key": key_label,
                            "aad": aad.hex() or "empty",
                            "pt_len": len(pt),
                            "pt_head": pt[:32].hex(),
                        }
                    )
                except Exception:
                    pass

    report["ok"] = bool(report["hits"])
    return report


def probe_samples(session: Any | None, bodies: list[bytes], *, max_samples: int = 3) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for body in bodies[:max_samples]:
        if len(body) != BODY_LEN:
            continue
        row = probe_body_decrypt(body, session)
        row["body_sha256_prefix"] = hashlib.sha256(body).hexdigest()[:16]
        out.append(row)
    return out
