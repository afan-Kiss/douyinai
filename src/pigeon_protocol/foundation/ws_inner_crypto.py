"""169B inner crypto — AES-GCM layout + KDF probes for encrypted_send variant."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from pigeon_protocol.foundation.ws_inner_proto import BODY_LEN, aes_gcm_layout

HKDF_INFO_LABELS = (
    b"",
    b"pigeon",
    b"edbX",
    b"PackedMessage",
    b"create_message",
    b"CreateMessage",
    b"PigeonImCreateMessage",
    b"access_token",
    b"imcloud",
    b"frontier",
)


def _enc_varint(value: int) -> bytes:
    out = bytearray()
    v = int(value)
    while v > 0x7F:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def seal_aes_gcm_body(key: bytes, plaintext: bytes, *, nonce: bytes, aad: bytes = b"") -> bytes:
    """Seal 161B body as 12B nonce + ciphertext + 16B tag (ring layout)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(nonce) != 12:
        raise ValueError("nonce must be 12 bytes")
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    body = nonce + ct
    if len(body) != BODY_LEN:
        raise ValueError(f"sealed body length {len(body)} != {BODY_LEN}")
    return body


def parse_encrypted_body(body: bytes) -> dict[str, Any]:
    layout = aes_gcm_layout(body)
    if not layout.get("ok"):
        return {"ok": False, "error": "bad body length"}
    nonce = body[:12]
    tag = body[-16:]
    ciphertext = body[12:-16]
    return {
        "ok": True,
        "nonce": nonce,
        "ciphertext": ciphertext,
        "tag": tag,
        "layout": layout,
    }


def _uuid_bytes(token: str) -> bytes | None:
    text = str(token or "").strip()
    if not text:
        return None
    try:
        return uuid.UUID(text).bytes
    except ValueError:
        return None


def derive_key_candidates(session: Any | None = None) -> list[tuple[str, bytes]]:
    """Expand KDF material — IM accessToken UUID + session cookies."""
    from pigeon_protocol.foundation.ws_inner_cipher_probe import _derive_key_candidates

    keys = _derive_key_candidates(session)

    materials: list[tuple[str, bytes]] = []
    extra = getattr(session, "extra", None) or {} if session is not None else {}
    invoke_path = Path(__file__).resolve().parents[3] / "analysis" / "feige_rust_invoke_latest.json"

    tokens: list[tuple[str, str]] = []
    for label in ("im_access_token", "rust_sdk_access_token"):
        val = extra.get(label)
        if val:
            tokens.append((label, str(val)))

    if invoke_path.is_file():
        try:
            doc = json.loads(invoke_path.read_text(encoding="utf-8"))
            steps = (doc.get("steps") or {})
            cu = steps.get("createUser") or {}
            full = cu.get("access_token_full") or cu.get("access_token")
            if full:
                tokens.append(("invoke_createUser", str(full)))
            for step in steps.values():
                if not isinstance(step, dict):
                    continue
                for k, v in step.items():
                    if "access_token" in k and isinstance(v, str) and len(v) > 20 and "..." not in v:
                        tokens.append((f"step_{k}", v))
        except (OSError, json.JSONDecodeError):
            pass

    seen = {raw for _, raw in materials}
    for label, token in tokens:
        raw = token.encode()
        if raw not in seen:
            materials.append((label, raw))
            seen.add(raw)
        ub = _uuid_bytes(token)
        if ub and ub not in seen:
            materials.append((f"{label}_uuid16", ub))
            materials.append((f"{label}_uuid", ub + ub[:16]))
            seen.add(ub)

    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError:
        return keys

    for label, raw in materials:
        for hkdf_len in (16, 32):
            for info in HKDF_INFO_LABELS:
                for salt in (b"", raw[:16], b"pigeon"):
                    try:
                        derived = HKDF(
                            algorithm=hashes.SHA256(),
                            length=hkdf_len,
                            salt=salt or None,
                            info=info,
                        ).derive(raw)
                        keys.append((f"hkdf_sha256({label},salt={salt[:4].hex()},info={info[:12]!r})", derived))
                    except Exception:
                        pass
        keys.append((f"sha256({label})[:16]", hashlib.sha256(raw).digest()[:16]))
        keys.append((f"sha256({label})", hashlib.sha256(raw).digest()))

    # de-dupe by key bytes
    out: list[tuple[str, bytes]] = []
    seen_key: set[bytes] = set()
    for label, key in keys:
        if key in seen_key:
            continue
        seen_key.add(key)
        out.append((label, key))
    return out


def probe_decrypt(body: bytes, session: Any | None = None) -> dict[str, Any]:
    from pigeon_protocol.foundation.ws_inner_cipher_probe import probe_body_decrypt

    report = probe_body_decrypt(body, session)
    layout = aes_gcm_layout(body)
    if layout.get("fits_ring_aead") and not report.get("hits"):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
        except ImportError:
            return report

        nonce = body[:12]
        ct_tag = body[12:]
        keys = derive_key_candidates(session)
        aads = [b"", body[:8], b"pigeon", b"edbX", b"PackedMessage"]
        for key_label, key in keys:
            for aad in aads:
                try:
                    pt = AESGCM(key).decrypt(nonce, ct_tag, aad)
                    report.setdefault("hits", []).append(
                        {
                            "cipher": "AES-GCM",
                            "key": key_label,
                            "aad": aad.hex() or "empty",
                            "pt_len": len(pt),
                            "pt_head": pt[:48].hex(),
                        }
                    )
                except Exception:
                    pass
                if len(key) == 32:
                    try:
                        pt = ChaCha20Poly1305(key).decrypt(nonce, ct_tag, aad)
                        report.setdefault("hits", []).append(
                            {
                                "cipher": "ChaCha20-Poly1305",
                                "key": key_label,
                                "aad": aad.hex() or "empty",
                                "pt_len": len(pt),
                                "pt_head": pt[:48].hex(),
                            }
                        )
                    except Exception:
                        pass
        report["keys_tried"] = len(keys) * len(aads) * 2
        report["ok"] = bool(report.get("hits"))
    return report


def probe_envelope_candidates(session, *, ts_start: int, ts_span: int) -> list[dict[str, Any]]:
    """Compare init timestamp-derived envelopes against cached template."""
    from pigeon_protocol.foundation.ws_inner_edbx import ENVELOPE_LEN, load_envelope_template

    tpl = load_envelope_template(session)
    target = tpl["envelope"] if tpl else None
    device_id = str(getattr(session, "device_id", "") or "")
    rows: list[dict[str, Any]] = []

    candidates: list[tuple[str, bytes]] = []
    from pigeon_protocol.foundation.ws_inner_edbx import envelope_from_init_timestamps

    candidates.append(("init_ts", envelope_from_init_timestamps(ts_start, ts_span, device_id=device_id)))

    extra = getattr(session, "extra", None) or {}
    token = str(extra.get("im_access_token") or extra.get("rust_sdk_access_token") or "")
    if token:
        ub = _uuid_bytes(token)
        if ub:
            buf = bytearray(ENVELOPE_LEN)
            buf[0:16] = hashlib.sha256(ub).digest()[:16]
            struct_pack_ts(buf, 8, ts_start)
            candidates.append(("uuid_sha256_prefix", bytes(buf)))

    for label, env in candidates:
        if len(env) != ENVELOPE_LEN:
            continue
        row = {"label": label, "envelope_hex": env.hex()}
        if target:
            diff = sum(1 for a, b in zip(env, target) if a != b)
            row["diff_bytes"] = diff
            row["match"] = diff == 0
        rows.append(row)
    return rows


def struct_pack_ts(buf: bytearray, offset: int, ts: int) -> None:
    import struct

    struct.pack_into("<Q", buf, offset, int(ts))
