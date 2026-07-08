"""Rust .node static RE — PE exports, crypto string xrefs, Ghidra hints."""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_NODE = ROOT / "analysis" / "feige_electron_sdk" / "rust-sdk-win32-x64-msvc" / "rust-sdk.win32-x64-msvc.node"
OUT_PATH = ROOT / "analysis" / "node_re_169b.json"

# Confirmed hits in rust-sdk.win32-x64-msvc.node (2026-07)
CRYPTO_STRINGS: tuple[tuple[str, str], ...] = (
    ("aes", "0xb1c07b"),
    ("gcm", "0xbd5284"),
    ("chacha", "0xd2dab3"),
    ("encrypt", "0xbc94fc"),
    ("nonce", "0xc5cfe4"),
    ("HMAC", "0xbde7e7"),
    ("sha256", "0xffa547"),
    ("ring::", "0xd2ab1a"),
    ("invalid access token", "0xb27a78"),
    ("access token", "0xb27b12"),
    ("create_message", "0xb4ee06"),
    ("CreateMessage", "0xd2ecad"),
    ("PigeonImCreateMessage", "0xd2ecad"),
    ("PackedMessage", "0xb18ce8"),
    ("169", "0xbc331d"),
    ("inner", "0xbc3d37"),
    ("ticket", "0xb349c0"),
    ("protobuf", "0xbb63e0"),
)

RE_ENTRYPOINTS: tuple[str, ...] = (
    "napi_register_module_v1",
    "molten_api_Api_new_with_config",
    "molten_logifier_Logifier_pack_and_upload",
)


def _read_cstring(data: bytes, off: int, limit: int = 256) -> str:
    end = data.find(b"\x00", off, off + limit)
    if end == -1:
        end = min(off + limit, len(data))
    return data[off:end].decode("ascii", errors="replace")


def parse_pe64_exports(data: bytes) -> list[dict[str, Any]]:
    if len(data) < 64 or data[:2] != b"MZ":
        return [{"error": "not PE"}]
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew : e_lfanew + 4] != b"PE\x00\x00":
        return [{"error": "bad PE signature"}]
    coff = e_lfanew + 4
    num_sections = struct.unpack_from("<H", data, coff + 2)[0]
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic != 0x20B:
        return [{"error": f"expected PE32+, got magic {hex(magic)}"}]
    dd_off = opt + 112
    export_rva, export_size = struct.unpack_from("<II", data, dd_off)
    if not export_rva:
        return [{"error": "no export directory"}]

    def rva_to_off(rva: int) -> int | None:
        sec_base = opt + opt_size
        for _ in range(num_sections):
            virtual_size, virtual_addr, raw_size, raw_ptr = struct.unpack_from("<IIII", data, sec_base + 8)
            if virtual_addr <= rva < virtual_addr + max(virtual_size, raw_size):
                return rva - virtual_addr + raw_ptr
            sec_base += 40
        return None

    exp_off = rva_to_off(export_rva)
    if exp_off is None:
        return [{"error": "export dir not mapped"}]

    (
        _char,
        timestamp,
        _maj,
        _min,
        _name_rva,
        ordinal_base,
        num_functions,
        num_names,
        addr_table_rva,
        name_ptr_rva,
        ordinal_table_rva,
    ) = struct.unpack_from("<IIHHIIIIIII", data, exp_off)

    addr_off = rva_to_off(addr_table_rva)
    names_off = rva_to_off(name_ptr_rva)
    ord_off = rva_to_off(ordinal_table_rva)
    if None in (addr_off, names_off, ord_off):
        return [{"error": "export tables not mapped"}]

    exports: list[dict[str, Any]] = []
    for i in range(num_names):
        name_rva = struct.unpack_from("<I", data, names_off + 4 * i)[0]
        name_off = rva_to_off(name_rva)
        if name_off is None:
            continue
        name = _read_cstring(data, name_off)
        ordinal = struct.unpack_from("<H", data, ord_off + 2 * i)[0]
        func_rva = struct.unpack_from("<I", data, addr_off + 4 * (ordinal - ordinal_base))[0]
        func_off = rva_to_off(func_rva)
        exports.append(
            {
                "name": name,
                "ordinal": ordinal,
                "rva": hex(func_rva),
                "file_offset": hex(func_off) if func_off is not None else None,
            }
        )
    return exports


def scan_string_context(data: bytes, needle: bytes, ctx: int = 48) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    idx = 0
    while len(hits) < 8:
        idx = data.find(needle, idx)
        if idx == -1:
            break
        chunk = data[max(0, idx - ctx) : idx + len(needle) + ctx]
        hits.append(
            {
                "offset": hex(idx),
                "needle": needle.decode("ascii", errors="replace"),
                "context": chunk.decode("ascii", errors="replace"),
            }
        )
        idx += len(needle)
    return hits


def node_re_report(path: Path | None = None) -> dict[str, Any]:
    node = path or DEFAULT_NODE
    if not node.is_file():
        return {"error": f"missing: {node}"}

    data = node.read_bytes()
    exports = parse_pe64_exports(data)
    export_names = {e.get("name") for e in exports if e.get("name")}

    crypto_hits: list[dict[str, Any]] = []
    for name, off_hex in CRYPTO_STRINGS:
        off = int(off_hex, 16)
        if off < len(data):
            ctx = data[max(0, off - 32) : off + len(name) + 64]
            crypto_hits.append(
                {
                    "name": name,
                    "offset": off_hex,
                    "context": ctx.decode("ascii", errors="replace")[:120],
                }
            )

    create_msg_ctx = scan_string_context(data, b"PigeonImCreateMessage", 64)
    access_token_ctx = scan_string_context(data, b"invalid access token", 48)

    report: dict[str, Any] = {
        "node": str(node),
        "size": len(data),
        "pe_exports": exports,
        "napi_entrypoints_present": {n: n in export_names for n in RE_ENTRYPOINTS},
        "crypto_string_xrefs": crypto_hits,
        "create_message_context": create_msg_ctx[:2],
        "access_token_context": access_token_ctx[:2],
        "ghidra_hints": [
            {"label": "CreateMessage dispatch", "file_offset": "0xd2ecad", "action": "Xref PigeonImCreateMessage → cmd 11327 handler"},
            {"label": "Access token gate", "file_offset": "0xb27a78", "action": "Xref invalid access token → IMInit token check"},
            {"label": "AES-GCM (ring)", "file_offset": "0xb1c07b", "action": "Xref aes/gcm strings → 161B body seal/open"},
            {"label": "Nonce layout", "file_offset": "0xc5cfe4", "action": "Confirm 12-byte nonce prefix in 161B body"},
            {"label": "NAPI invoke", "file_offset": None, "action": "Break on invokeAsync export → dump pre/post 169B buffer"},
        ],
        "pure_python_blockers": [
            "161B send body uses ring AES-GCM; KDF from IM accessToken not recovered",
            "8B class header independent of body within session (unified mode shares body)",
            "edbX variant is INIT-only plaintext — different code path from send encrypt",
        ],
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
