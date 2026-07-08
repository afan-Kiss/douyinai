"""Tests for init field-6 edbX trailer extraction."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.foundation.init_edbx_seeds import (  # noqa: E402
    extract_edbx_trailer_from_init,
)
from pigeon_protocol.pure_config import STANDALONE_BUNDLE

KNOWN = bytes.fromhex("b38a848485e5c1a1")


def test_extract_trailer_from_bundle_init():
    path = STANDALONE_BUNDLE / "get_message_by_init_response.bin"
    if not path.is_file():
        return
    raw = path.read_bytes()
    trailer, via, report = extract_edbx_trailer_from_init(raw)
    assert trailer == KNOWN
    assert via == "init_field6_pigeon"
    assert report.get("field6_len", 0) > 1000
    assert len(report.get("seeds") or []) >= 1


def test_pigeon_distance_ranks_correct_seed():
    path = STANDALONE_BUNDLE / "get_message_by_init_response.bin"
    if not path.is_file():
        return
    raw = path.read_bytes()
    _, _, report = extract_edbx_trailer_from_init(raw)
    seeds = report.get("seeds") or []
    assert seeds[0]["trailer_hex"] == KNOWN.hex()
    assert seeds[0]["pigeon_dist"] < 128
    assert seeds[0]["nested_f21_hex"] == "9c0c08"
    assert seeds[0]["session_nonce_hex"] == "8a86aadd94d5946a"
    assert seeds[0]["score"] >= 1100


def test_prefix8_not_live_field12_prefix():
    from pigeon_protocol.foundation.init_edbx_seeds import analyze_prefix8_vs_field12
    from pigeon_protocol.pure_config import STANDALONE_BUNDLE

    path = STANDALONE_BUNDLE / "get_message_by_init_response.bin"
    if not path.is_file():
        return
    raw = path.read_bytes()
    report = analyze_prefix8_vs_field12(raw)
    assert report["nonce_equals_live_derived"] is False
    assert report["template_session_nonce_hex"] == "8a86aadd94d5946a"
    assert report["live_field12_formula"] == "derive_prefix_from_ts_us(init_field10)"
