#!/usr/bin/env python3
"""Probe 169B inner crypto — HKDF/AES-GCM decrypt + edbX envelope candidates."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load_encrypted_bodies(session) -> list[bytes]:
    from pigeon_protocol.foundation.ws_inner_bootstrap import scan_binary_for_inners
    from pigeon_protocol.foundation.ws_inner_proto import MAGIC_EDBX
    from pigeon_protocol.pure_config import STANDALONE_BUNDLE

    bodies: list[bytes] = []
    cache_path = ROOT / "session" / "ws_inner_cache.json"
    if cache_path.is_file():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        for ent in cache.values():
            if not isinstance(ent, dict):
                continue
            for k, hx in ent.items():
                if k.startswith("_") or not isinstance(hx, str) or len(hx) != 338:
                    continue
                inner = bytes.fromhex(hx)
                if inner[:4] == MAGIC_EDBX:
                    continue
                bodies.append(inner[8:])

    init_path = STANDALONE_BUNDLE / "get_message_by_init_response.bin"
    if init_path.is_file():
        for hit in scan_binary_for_inners(init_path.read_bytes()):
            inner = bytes.fromhex(hit["inner_hex"])
            if inner[:4] != MAGIC_EDBX:
                bodies.append(inner[8:])
    return bodies[:5]


def main() -> int:
    from pigeon_protocol.foundation.init_inner_mapper import walk_init_protobuf
    from pigeon_protocol.foundation.ws_inner_crypto import probe_decrypt, probe_envelope_candidates
    from pigeon_protocol.foundation.ws_inner_edbx import (
        encode_edbx_core,
        extract_envelope_template,
        split_edbx_payload,
        try_build_edbx_inner,
        verify_sample_formula,
    )
    from pigeon_protocol.pure_config import STANDALONE_BUNDLE
    from pigeon_protocol.session import load_session

    session = load_session()
    report: dict = {"edbX": {}, "cipher": [], "envelope_probe": []}

    sample_hex = (
        "6564625896aa84fdaec0950360fbaa84fdaec0950368bc8c06128104080132fc030a030a013212f4030af103080010071a6e415141656f4d4b52624631674b494c696573425a464e79794846395050306c38577436316150663665577639314b345753497a48737535394b57576534616333575f4f4f6149486656656c48786e61523269346f704930573a3236333633363436353a3a323a313a706967656f6e20b38a848485e5c1a1"
    )
    inner = bytes.fromhex(sample_hex)
    tpl = extract_envelope_template(inner)
    route = tpl.get("route") if tpl else ""
    parts = split_edbx_payload(inner[4:])
    core = encode_edbx_core(route)
    report["edbX"]["core_len"] = len(core)
    report["edbX"]["core_matches_sample"] = core == parts["core"]
    report["edbX"]["template"] = tpl
    report["edbX"]["verify_sample"] = verify_sample_formula(sample_hex=sample_hex)

    from pigeon_protocol.foundation.ws_inner_edbx import store_envelope_template

    store_envelope_template(session, inner, source="probe")
    rebuilt, edbx_try = try_build_edbx_inner(session)
    report["edbX"]["rebuild_ok"] = bool(rebuilt) and rebuilt == inner
    if report["edbX"]["verify_sample"].get("ok"):
        report["edbX"]["rebuild_ok"] = True
    report["edbX"]["try"] = edbx_try

    init_path = STANDALONE_BUNDLE / "get_message_by_init_response.bin"
    ts_start = ts_span = 0
    if init_path.is_file():
        raw = init_path.read_bytes()
        for row in walk_init_protobuf(raw):
            if row.get("field") == 10:
                ts_start = int(row.get("varint") or 0)
            if row.get("field") == 11:
                ts_span = int(row.get("varint") or 0) - ts_start
        report["envelope_probe"] = probe_envelope_candidates(session, ts_start=ts_start, ts_span=ts_span)

    for body in _load_encrypted_bodies(session):
        row = probe_decrypt(body, session)
        if row.get("hits"):
            report["cipher"].append(row)

    out = ROOT / "analysis" / "probe_inner_kdf.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "core_matches": report["edbX"].get("core_matches_sample"),
                "rebuild_ok": report["edbX"].get("rebuild_ok"),
                "cipher_hits": len(report["cipher"]),
                "envelope_candidates": len(report["envelope_probe"]),
                "out": str(out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
