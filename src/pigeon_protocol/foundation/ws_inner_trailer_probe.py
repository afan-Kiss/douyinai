"""Exhaustive edbX 8B trailer derivation probes."""
from __future__ import annotations

import hashlib
import hmac
import json
import struct
import uuid
import zlib
from pathlib import Path
from typing import Any

TARGET = bytes.fromhex("b38a848485e5c1a1")
ROOT = Path(__file__).resolve().parents[3]

HMAC_KEYS = (
    b"",
    b"pigeon",
    b"edbX",
    b"imcloud",
    b"frontier",
    b"access_token",
    b"PackedMessage",
    b"create_message",
    b"CreateMessage",
    b"jinritemai",
    b"1383",
)


def _hit(label: str, val: bytes, hits: list[dict[str, str]]) -> None:
    if len(val) == 8 and val == TARGET:
        hits.append({"label": label, "hex": val.hex()})


def _digest_candidates(data: bytes, prefix: str, hits: list[dict[str, str]]) -> None:
    if not data:
        return
    _hit(f"{prefix}:sha256_tail8", hashlib.sha256(data).digest()[-8:], hits)
    _hit(f"{prefix}:sha256_head8", hashlib.sha256(data).digest()[:8], hits)
    _hit(f"{prefix}:md5_tail8", hashlib.md5(data).digest()[-8:], hits)
    _hit(f"{prefix}:md5_head8", hashlib.md5(data).digest()[:8], hits)
    _hit(f"{prefix}:sha1_tail8", hashlib.sha1(data).digest()[-8:], hits)
    crc = zlib.crc32(data) & 0xFFFFFFFF
    _hit(f"{prefix}:crc32_be", struct.pack(">I", crc) + b"\x00\x00\x00\x00", hits)
    _hit(f"{prefix}:crc32_le_pad", struct.pack("<I", crc) + b"\x00\x00\x00\x00", hits)
    for key in HMAC_KEYS:
        tag = key.decode("ascii", "replace") or "empty"
        _hit(f"{prefix}:hmac_sha256_{tag}_tail8", hmac.new(key, data, hashlib.sha256).digest()[-8:], hits)
        _hit(f"{prefix}:hmac_md5_{tag}_tail8", hmac.new(key, data, hashlib.md5).digest()[-8:], hits)


def collect_materials(session=None) -> list[tuple[str, bytes]]:
    mats: list[tuple[str, bytes]] = []
    if session is not None:
        if session.device_id:
            mats.append(("device_id", str(session.device_id).encode()))
        if session.shop_id:
            mats.append(("shop_id", str(session.shop_id).encode()))
        for k, v in (session.cookies or {}).items():
            if v and k.lower() in (
                "sessionid",
                "sid_tt",
                "pigeon_cid",
                "uid_tt",
                "shop_id",
                "s_v_web_id",
            ):
                mats.append((f"cookie_{k}", str(v).encode()))
        for k, v in (session.query_tokens or {}).items():
            if v:
                mats.append((f"qt_{k}", str(v).encode()))
        extra = getattr(session, "extra", None) or {}
        tok = str(extra.get("im_access_token") or "")
        if tok:
            mats.append(("session_token", tok.encode()))
            try:
                u = uuid.UUID(tok)
                mats.append(("session_uuid16", u.bytes))
                mats.append(("session_uuid_le16", u.bytes_le))
            except ValueError:
                pass

    for path in (
        ROOT / "analysis" / "feige_create_user_latest.json",
        ROOT / "analysis" / "feige_rust_invoke_latest.json",
        ROOT / "analysis" / "invoke_after_qr.json",
    ):
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
            if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                text = raw.decode("utf-16", errors="replace")
            else:
                text = raw.decode("utf-8", errors="replace")
            doc = json.loads(text)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        tok = str(doc.get("access_token") or "")
        cu = (doc.get("steps") or {}).get("createUser") or {}
        if not tok:
            tok = str(cu.get("access_token_full") or "")
        if tok:
            try:
                u = uuid.UUID(tok)
                mats.append((f"uuid16_{path.stem}", u.bytes))
                mats.append((f"uuid_le_{path.stem}", u.bytes_le))
            except ValueError:
                pass
        rh = str(cu.get("resp_hex_head") or "")
        if rh:
            try:
                mats.append((f"createUser_resp_{path.stem}", bytes.fromhex(rh)))
            except ValueError:
                pass

    if session is not None:
        try:
            from pigeon_protocol.foundation.init_timestamps import load_init_bytes, parse_init_timestamps

            raw, _ = load_init_bytes(session)
            if raw:
                mats.append(("init_raw", raw))
                ts = parse_init_timestamps(raw)
                if ts.get("ts_start"):
                    t = int(ts["ts_start"])
                    mats.append(("init_f10_le", t.to_bytes(8, "little")))
                    mats.append(("init_f10_be", t.to_bytes(8, "big")))
        except Exception:
            pass

    return mats


def probe_trailer(session=None, *, target: bytes = TARGET) -> dict[str, Any]:
    global TARGET
    TARGET = target
    hits: list[dict[str, str]] = []
    mats = collect_materials(session)

    for name, data in mats:
        _digest_candidates(data, name, hits)

    # pairwise HMAC / concat (bounded)
    small = [(n, d) for n, d in mats if d and len(d) <= 256][:24]
    for i, (na, a) in enumerate(small):
        for nb, b in small[i + 1 : i + 9]:
            _digest_candidates(a + b, f"concat:{na}+{nb}", hits)
            _hit(f"hmac_key_{na}_msg_{nb}", hmac.new(a, b, hashlib.sha256).digest()[-8:], hits)
            _hit(f"hmac_key_{nb}_msg_{na}", hmac.new(b, a, hashlib.sha256).digest()[-8:], hits)

    # body-without-trailer MAC (sample outer hash)
    sample_hex = (
        "6564625896aa84fdaec0950360fbaa84fdaec0950368bc8c06128104080132fc030a030a013212f4030af103"
        "080010071a6e415141656f4d4b52624631674b494c696573425a464e79794846395050306c38577436316150663665577639314"
        "b345753497a48737535394b57576534616333575f4f4f6149486656656c48786e61523269346f704930573a323633363336343635"
        "3a3a323a313a706967656f6e20"
    )
    try:
        body = bytes.fromhex(sample_hex)
        _digest_candidates(body, "sample_body157", hits)
        _digest_candidates(body[:8], "sample_prefix8", hits)
        for name, data in mats:
            if len(data) <= 64:
                _digest_candidates(body + data, f"body+{name}", hits)
                _digest_candidates(data + body, f"{name}+body", hits)
    except ValueError:
        pass

    return {
        "target_hex": target.hex(),
        "material_count": len(mats),
        "materials": [{"name": n, "len": len(d)} for n, d in mats],
        "hits": hits,
        "hit_count": len(hits),
    }
