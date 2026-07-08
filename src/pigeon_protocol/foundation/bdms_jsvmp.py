"""bdms jsvmp inflated bytecode — pure-Python structure parser (no browser)."""
from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BIN = ROOT / "analysis" / "bdms_jsvmp.bin"
DEC = ROOT / "analysis" / "bdms_jsvmp_dec.bin"
INF = ROOT / "analysis" / "bdms_jsvmp_inflated.bin"


@dataclass
class ReadCursor:
    data: bytes
    i: int = 0

    def read_u8(self) -> int:
        if self.i >= len(self.data):
            raise EOFError(f"read past end @ {self.i}/{len(self.data)}")
        b = self.data[self.i]
        self.i += 1
        return b


def xor_byte(char_code: int, key: int, index: int) -> int:
    return (char_code ^ ((key + (key % 10) * index) % 256)) & 0xFF


def decrypt_jsvmp(raw: bytes) -> bytes:
    if len(raw) < 8:
        raise ValueError("jsvmp payload too short")
    key = sum(raw[i] for i in range(4, 8)) % 256
    body = raw[8:]
    return bytes(xor_byte(b, key, i) for i, b in enumerate(body))


def inflate_jsvmp(decrypted: bytes) -> bytes:
    return zlib.decompress(decrypted, -zlib.MAX_WBITS)


def read_leb128_signed(cur: ReadCursor) -> int:
    """bdms.js function J(t) — signed LEB128."""
    result = 0
    shift = 0
    while True:
        b = cur.read_u8()
        result |= (b & 0x7F) << shift
        shift += 7
        if not (b & 0x80):
            if shift < 32 and (b & 0x40):
                result |= - (1 << shift)
            return result


def read_utf8_string(cur: ReadCursor) -> str:
    """bdms.js function Q(t) — UTF-8 string from bytecode."""
    chars: list[int] = []
    state = -1
    while True:
        n = cur.read_u8()
        if 128 <= n < 192:
            if state < 0:
                raise ValueError(f"utf8 continuation without start @ {cur.i - 1}")
            state = (state << 6) + (n & 0x3F)
        else:
            if state >= 0:
                chars.append(state)
            if n < 128:
                state = n
            elif n < 224:
                state = n & 0x1F
            elif n < 240:
                state = n & 0x0F
            elif n < 248:
                state = n & 0x07
            else:
                break
    return "".join(chr(c) for c in chars)


@dataclass
class TryCatchEntry:
    start: int
    end: int
    handler: int
    finally_or_end: int


@dataclass
class VmFunction:
    index: int
    arg_count: int
    strict: bool
    bytecode: list[int]
    try_catch: list[TryCatchEntry]
    opcode_hist: dict[int, int] = field(default_factory=dict)

    def summarize(self) -> dict[str, Any]:
        top_ops = sorted(self.opcode_hist.items(), key=lambda x: -x[1])[:12]
        return {
            "index": self.index,
            "arg_count": self.arg_count,
            "strict": self.strict,
            "bytecode_len": len(self.bytecode),
            "try_catch_count": len(self.try_catch),
            "top_opcodes": top_ops,
        }


@dataclass
class JsvmpProgram:
    string_pool: list[str]
    functions: list[VmFunction]
    bytes_consumed: int
    bytes_total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "string_pool_count": len(self.string_pool),
            "string_pool_sample": self.string_pool[:40],
            "string_pool_tail": self.string_pool[-10:] if len(self.string_pool) > 10 else [],
            "function_count": len(self.functions),
            "functions": [f.summarize() for f in self.functions],
            "bytes_consumed": self.bytes_consumed,
            "bytes_total": self.bytes_total,
            "bytes_remaining": self.bytes_total - self.bytes_consumed,
        }


def parse_inflated(data: bytes) -> JsvmpProgram:
    cur = ReadCursor(data)
    pool_count = read_leb128_signed(cur)
    string_pool = [read_utf8_string(cur) for _ in range(pool_count)]

    fn_count = read_leb128_signed(cur)
    functions: list[VmFunction] = []
    for fi in range(fn_count):
        arg_count = read_leb128_signed(cur)
        strict = bool(read_leb128_signed(cur))
        tc_count = read_leb128_signed(cur)
        try_catch: list[TryCatchEntry] = []
        for _ in range(tc_count):
            a, b, c, d = (read_leb128_signed(cur) for _ in range(4))
            try_catch.append(TryCatchEntry(a, b, c, d))
        bc_count = read_leb128_signed(cur)
        bytecode = [read_leb128_signed(cur) for _ in range(bc_count)]
        hist: dict[int, int] = {}
        for op in bytecode:
            hist[op] = hist.get(op, 0) + 1
        functions.append(
            VmFunction(
                index=fi,
                arg_count=arg_count,
                strict=strict,
                bytecode=bytecode,
                try_catch=try_catch,
                opcode_hist=hist,
            )
        )

    return JsvmpProgram(
        string_pool=string_pool,
        functions=functions,
        bytes_consumed=cur.i,
        bytes_total=len(data),
    )


def load_program() -> tuple[JsvmpProgram, dict[str, Any]]:
    raw = BIN.read_bytes() if BIN.exists() else b""
    if not raw:
        raise FileNotFoundError(f"missing {BIN}; run scripts/analyze_bdms_jsvmp.py")
    dec = decrypt_jsvmp(raw)
    inflated = inflate_jsvmp(dec)
    prog = parse_inflated(inflated)
    meta = {
        "raw_len": len(raw),
        "decrypted_len": len(dec),
        "inflated_len": len(inflated),
        "xor_key": sum(raw[i] for i in range(4, 8)) % 256,
    }
    return prog, meta


def keyword_scan(pool: list[str]) -> dict[str, list[str]]:
    keys = (
        "a_bogus",
        "bogus",
        "msToken",
        "verifyFp",
        "fetch",
        "XMLHttpRequest",
        "location",
        "navigator",
        "userAgent",
        "encodeURIComponent",
        "sign",
        "crypto",
    )
    out: dict[str, list[str]] = {}
    low = [s.lower() for s in pool]
    for k in keys:
        hits = [pool[i] for i, s in enumerate(low) if k.lower() in s]
        if hits:
            out[k] = hits[:8]
    return out


def opcode_map_from_js() -> dict[int, str]:
    """Heuristic labels from bdms.js VM executor X() dispatch tree."""
    return {
        0: "strict_eq",
        1: "lt",
        2: "post_dec_prop",
        3: "define_prop_val",
        4: "return",
        5: "to_number",
        6: "typeof_global",
        7: "ensure_global",
        9: "jmp",
        10: "get_prop",
        11: "load_closure",
        12: "mod_assign",
        13: "typeof",
        14: "throw",
        16: "shl",
        17: "dup",
        18: "bind_fn",
        19: "object_new",
        21: "load_str",
        22: "set_prop",
        23: "push_false",
        24: "neg",
        25: "sub_assign",
        26: "delete_prop",
        27: "neq",
        28: "throw",
        29: "gt",
        30: "set_prop_str",
        31: "jmp_if_false",
        32: "spread",
        33: "pre_dec_prop",
        34: "jmp_if_true",
        35: "cond_jmp",
        36: "call",
        37: "mul_assign",
        38: "eq",
        39: "switch_eq_jmp",
        40: "load_global",
        41: "instanceof",
        42: "ushr",
        43: "define_setter",
        44: "push_nan",
        45: "push_true",
        46: "load_prop_depth",
        47: "finally",
        48: "define_getter",
        49: "push_undef",
        50: "xor",
        51: "push_const",
        52: "shr",
        53: "le",
        54: "seq",
        55: "in",
        56: "bitnot",
        57: "push_undefined",
        58: "pre_inc_prop",
        59: "add_assign",
        60: "not",
        61: "post_inc_prop",
        62: "or",
        63: "load_this",
        64: "ge",
        65: "for_in_next",
        66: "div_assign",
    }


def _disasm_ops(bytecode: list[int]) -> list[tuple[int, int, int | None]]:
    """Walk bytecode; return (pc, opcode, operand?) tuples."""
    ops: list[tuple[int, int, int | None]] = []
    pc = 0
    while pc < len(bytecode):
        op = bytecode[pc]
        pc += 1
        operand: int | None = None
        # opcodes that consume next leb as operand (from X() dispatch)
        if op in (
            3, 4, 6, 7, 9, 11, 16, 18, 21, 22, 30, 31, 32, 33, 34, 35, 36, 39,
            40, 43, 46, 48, 51, 65, 72, 75,
        ):
            if pc < len(bytecode):
                operand = bytecode[pc]
                pc += 1
        ops.append((pc - (2 if operand is not None else 1), op, operand))
    return ops


def find_string_xrefs(prog: JsvmpProgram, needles: list[str]) -> list[dict[str, Any]]:
    """Map string pool hits → VM functions referencing them via load_str/load_global."""
    idx_map: dict[str, list[int]] = {}
    for i, s in enumerate(prog.string_pool):
        for n in needles:
            if n.lower() in s.lower():
                idx_map.setdefault(n, []).append(i)

    all_needle_idxs = {i for ids in idx_map.values() for i in ids}
    xrefs: list[dict[str, Any]] = []
    for fn in prog.functions:
        hits: list[dict[str, Any]] = []
        for pc, op, operand in _disasm_ops(fn.bytecode):
            if operand is None:
                continue
            if op == 21 and operand in all_needle_idxs:  # load_str
                hits.append({"pc": pc, "op": "load_str", "str": prog.string_pool[operand]})
            elif op == 40 and operand < len(prog.string_pool):  # load_global
                name = prog.string_pool[operand]
                if any(n.lower() in name.lower() for n in needles):
                    hits.append({"pc": pc, "op": "load_global", "str": name})
        if hits:
            xrefs.append({"fn": fn.index, "arg_count": fn.arg_count, "bytecode_len": len(fn.bytecode), "hits": hits[:20]})
    return xrefs


def deep_report(prog: JsvmpProgram) -> dict[str, Any]:
    op_labels = opcode_map_from_js()
    fn_sizes = [len(f.bytecode) for f in prog.functions]
    all_ops: dict[int, int] = {}
    for f in prog.functions:
        for op, cnt in f.opcode_hist.items():
            all_ops[op] = all_ops.get(op, 0) + cnt

    top_global = sorted(all_ops.items(), key=lambda x: -x[1])[:20]
    sign_needles = ["a_bogus", "msToken", "handleUrl", "verifyFp", "bdmsInvokeList", "fetch"]
    xrefs = find_string_xrefs(prog, sign_needles)
    return {
        "program": prog.to_dict(),
        "keywords": keyword_scan(prog.string_pool),
        "sign_xrefs": xrefs[:25],
        "opcode_top20": [
            {"op": op, "count": cnt, "label": op_labels.get(op, "?")} for op, cnt in top_global
        ],
        "bytecode_stats": {
            "min_fn": min(fn_sizes) if fn_sizes else 0,
            "max_fn": max(fn_sizes) if fn_sizes else 0,
            "total_ops": sum(fn_sizes),
        },
        "interesting_strings": [
            s
            for s in prog.string_pool
            if any(
                k in s.lower()
                for k in ("bogus", "token", "sign", "fetch", "url", "query", "ms", "fp", "encrypt")
            )
        ][:30],
    }
