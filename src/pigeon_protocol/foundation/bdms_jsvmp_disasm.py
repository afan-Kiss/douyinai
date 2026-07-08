"""bdms jsvmp VM disassembler — operand layout from bdms.js X() v1.0.1.20."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pigeon_protocol.foundation.bdms_jsvmp import JsvmpProgram, VmFunction

# (name, operand_count)
OP_LAYOUT: dict[int, tuple[str, int]] = {
    0: ("STRICT_EQ", 0),
    1: ("LT", 0),
    2: ("POST_DEC_PROP", 0),
    3: ("DEFINE_PROP_VAL", 1),
    4: ("RETURN", 1),
    5: ("TO_NUMBER", 0),
    6: ("TYPEOF_GLOBAL", 1),
    7: ("ENSURE_GLOBAL", 1),
    8: ("PUSH_SCOPE_PAIR", 2),
    9: ("JMP", 1),
    10: ("GET_PROP", 0),
    11: ("LOAD_CLOSURE", 1),
    12: ("MOD_ASSIGN", 0),
    13: ("TYPEOF", 0),
    14: ("THROW", 0),
    15: ("DIV_ASSIGN", 0),
    16: ("SHL", 0),
    17: ("DUP", 0),
    18: ("BIND_FN", 1),
    19: ("NEW_OBJECT", 0),
    21: ("LOAD_STR", 1),
    22: ("SET_PROP", 0),
    23: ("PUSH_FALSE", 0),
    24: ("NEG", 0),
    25: ("SUB_ASSIGN", 0),
    26: ("DELETE_PROP", 0),
    27: ("NEQ", 0),
    28: ("THROW2", 0),
    29: ("GT", 0),
    30: ("SET_PROP_STR", 1),
    31: ("JMP_IF_FALSE", 1),
    32: ("SPREAD", 1),
    33: ("PRE_DEC_PROP", 0),
    34: ("JMP_IF_TRUE", 1),
    35: ("COND_JMP", 1),
    36: ("CALL", 1),
    37: ("MUL_ASSIGN", 0),
    38: ("EQ", 0),
    39: ("SWITCH_EQ_JMP", 1),
    40: ("LOAD_GLOBAL", 1),
    41: ("INSTANCEOF", 0),
    42: ("USHR", 0),
    43: ("DEFINE_SETTER", 1),
    44: ("PUSH_NAN", 0),
    45: ("PUSH_TRUE", 0),
    46: ("LOAD_SCOPE_PROP", 2),
    47: ("FINALLY", 0),
    48: ("DEFINE_GETTER", 1),
    49: ("SET_UNDEF", 0),
    50: ("XOR", 0),
    51: ("STORE_SCOPE", 2),
    52: ("SHR", 0),
    53: ("LE", 0),
    54: ("SNE", 0),
    55: ("IN", 0),
    56: ("BITNOT", 0),
    57: ("PUSH_UNDEFINED", 0),
    58: ("PRE_INC_PROP", 0),
    59: ("ADD_ASSIGN", 0),
    60: ("NOT", 0),
    61: ("POST_INC_PROP", 0),
    62: ("OR", 0),
    63: ("LOAD_THIS", 0),
    64: ("GE", 0),
    65: ("FOR_IN_NEXT", 1),
    66: ("PUSH_INFINITY", 0),
    67: ("SET_GLOBAL", 1),
    68: ("AND", 0),
    69: ("FOR_IN_INIT", 1),
    70: ("TRUTHY_JMP", 1),
    71: ("GET_PROP_STR", 1),
    72: ("PUSH_NUM", 1),
    73: ("PUSH_STR_AS_NUM", 1),
    74: ("NEW", 1),
    75: ("POP", 0),
    76: ("PUSH_NULL", 0),
}


@dataclass
class Insn:
    pc: int
    op: int
    name: str
    operands: list[int]
    comment: str = ""


def _pool_str(pool: list[str], idx: int) -> str:
    if 0 <= idx < len(pool):
        s = pool[idx]
        return s if len(s) <= 64 else s[:61] + "..."
    return f"?#{idx}"


def disassemble(bytecode: list[int], pool: list[str]) -> list[Insn]:
    out: list[Insn] = []
    pc = 0
    while pc < len(bytecode):
        start = pc
        op = bytecode[pc]
        pc += 1
        layout = OP_LAYOUT.get(op, (f"OP_{op}", 0))
        name, nops = layout
        operands: list[int] = []
        for _ in range(nops):
            if pc >= len(bytecode):
                break
            operands.append(bytecode[pc])
            pc += 1
        comment = _comment(op, operands, pool, start)
        out.append(Insn(start, op, name, operands, comment))
    return out


def _comment(op: int, operands: list[int], pool: list[str], pc: int) -> str:
    if op == 21 and operands:
        return repr(_pool_str(pool, operands[0]))
    if op in (3, 6, 7, 30, 40, 43, 48, 67, 71) and operands:
        return _pool_str(pool, operands[0])
    if op in (11, 18, 69) and operands:
        return f"fn#{operands[0]}"
    if op == 36 and operands:
        return f"argc={operands[0]}"
    if op == 74 and operands:
        return f"new_argc={operands[0]}"
    if op == 72 and operands:
        return f"num={operands[0]}"
    if op == 73 and operands:
        return f"+{_pool_str(pool, operands[0])!r}"
    if op in (4, 9, 31, 34, 35, 39, 70) and operands:
        return f"→@{pc + 1 + operands[0]}"
    if op == 46 and len(operands) >= 2:
        return f"depth={operands[0]} {_pool_str(pool, operands[1])!r}"
    if op == 51 and len(operands) >= 2:
        return f"depth={operands[0]} {_pool_str(pool, operands[1])!r}"
    if op == 8 and len(operands) >= 2:
        return f"depth={operands[0]} {_pool_str(pool, operands[1])!r}"
    return ""


def format_disasm(insns: list[Insn], *, limit: int = 0) -> str:
    rows = insns[:limit] if limit else insns
    return "\n".join(
        f"{i.pc:4d}  {i.op:3d} {i.name:18s} {' '.join(str(x) for x in i.operands):12s}{('; ' + i.comment) if i.comment else ''}"
        for i in rows
    )


def disasm_function(prog: JsvmpProgram, fn_index: int) -> dict[str, Any]:
    fn = prog.functions[fn_index]
    insns = disassemble(fn.bytecode, prog.string_pool)
    return {
        "index": fn_index,
        "arg_count": fn.arg_count,
        "strict": fn.strict,
        "bytecode_len": len(fn.bytecode),
        "try_catch": [
            {"start": t.start, "end": t.end, "handler": t.handler, "fin": t.finally_or_end}
            for t in fn.try_catch
        ],
        "disasm_text": format_disasm(insns),
        "insns": [
            {"pc": i.pc, "op": i.op, "name": i.name, "operands": i.operands, "comment": i.comment}
            for i in insns
        ],
    }


def call_graph(insns: list[Insn]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for i in insns:
        if i.op == 36 and i.operands:
            calls.append({"pc": i.pc, "type": "call", "argc": i.operands[0]})
        elif i.op in (11, 18) and i.operands:
            calls.append({"pc": i.pc, "type": "load_fn", "fn": i.operands[0]})
        elif i.op == 74 and i.operands:
            calls.append({"pc": i.pc, "type": "new", "argc": i.operands[0]})
    return calls


def sign_flow_summary(prog: JsvmpProgram, fn_index: int) -> dict[str, Any]:
    fn = prog.functions[fn_index]
    insns = disassemble(fn.bytecode, prog.string_pool)
    strings: list[dict[str, Any]] = []
    globals_: list[str] = []
    callees: list[int] = []
    for i in insns:
        if i.op == 21 and i.operands:
            strings.append({"pc": i.pc, "s": _pool_str(prog.string_pool, i.operands[0])})
        elif i.op == 40 and i.operands:
            globals_.append(_pool_str(prog.string_pool, i.operands[0]))
        elif i.op in (11, 18) and i.operands:
            callees.append(i.operands[0])
    return {
        "fn": fn_index,
        "arg_count": fn.arg_count,
        "strings": strings,
        "globals": globals_,
        "callees": sorted(set(callees)),
        "calls": call_graph(insns),
        "disasm_head": format_disasm(insns, limit=40),
        "disasm_around_abogus": _slice_around(insns, "a_bogus"),
    }


def _slice_around(insns: list[Insn], needle: str, ctx: int = 8) -> str:
    hits = [i for i in insns if needle in i.comment]
    if not hits:
        return ""
    h = hits[0]
    idx = insns.index(h)
    lo = max(0, idx - ctx)
    hi = min(len(insns), idx + ctx + 1)
    return format_disasm(insns[lo:hi])


def entry_points(prog: JsvmpProgram) -> list[int]:
    """W(idx) invocations visible in bdms.js tail — hardcoded hot entries."""
    return [156, 202, 281, 612, 685, 730]
