"""Unit tests for edbX 169B inner pure-Python formula."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.foundation.ws_inner_edbx import (  # noqa: E402
    CORE_LEN,
    CORE_SUFFIX,
    FIELD2_BLOB_LEN,
    INNER_LEN,
    PAYLOAD_LEN,
    PREFIX_XOR_MASK,
    ROUTE_PAD_LEN,
    SAMPLE_INNER_HEX,
    TRAILER_LEN,
    build_edbx_inner_derived,
    decode_ts_us_from_prefix,
    derive_prefix_from_ts_us,
    encode_edbx_core,
    normalize_route,
    normalize_trailer,
    split_edbx_payload,
    verify_sample_formula,
)

SAMPLE_ROUTE = (
    "AQAeoMKRbF1gKILiesBZFNyyHF9PP0l8Wt61aPf6eWv91K4WSIzHsu59KWWe4ac3W_OOaIHfVelHxnaR2i4opI0W"
    ":263636465::2:1:pigeon"
)
SAMPLE_F12 = 1783420470498683
SAMPLE_TRAILER = bytes.fromhex("b38a848485e5c1a1")


def test_sample_bit_exact_rebuild():
    report = verify_sample_formula()
    assert report["ok"] is True
    assert report["prefix_match"] is True
    assert report["core_len"] == CORE_LEN
    assert report["trailer_hex"] == SAMPLE_TRAILER.hex()


def test_prefix_xor_mask():
    pref = derive_prefix_from_ts_us(SAMPLE_F12)
    assert len(pref) == 8
    assert (pref[0] ^ PREFIX_XOR_MASK) == 0xFB
    assert decode_ts_us_from_prefix(pref) == SAMPLE_F12


def test_route_fixed_width():
    raw = normalize_route(SAMPLE_ROUTE)
    assert len(raw) == ROUTE_PAD_LEN
    assert raw.decode("ascii").endswith(":pigeon")


def test_core_suffix_and_blob_len():
    core = encode_edbx_core(SAMPLE_ROUTE)
    assert len(core) == CORE_LEN
    assert core.endswith(CORE_SUFFIX)
    assert len(core) - len(CORE_SUFFIX) == 116


def test_payload_layout():
    sample = bytes.fromhex(SAMPLE_INNER_HEX)
    assert len(sample) == INNER_LEN
    parts = split_edbx_payload(sample[4:])
    assert len(parts["prefix"]) == 8
    assert len(parts["outer"]) == 149
    assert len(parts["trailer"]) == TRAILER_LEN
    assert len(parts["field2_blob"]) == FIELD2_BLOB_LEN
    assert len(sample[4:]) == PAYLOAD_LEN


def test_normalize_trailer_legacy_nine_byte():
    legacy = bytes.fromhex("20" + SAMPLE_TRAILER.hex())
    assert normalize_trailer(legacy) == SAMPLE_TRAILER


def test_build_matches_sample():
    inner = build_edbx_inner_derived(SAMPLE_ROUTE, ts_us=SAMPLE_F12, trailer=SAMPLE_TRAILER)
    assert inner == bytes.fromhex(SAMPLE_INNER_HEX)
