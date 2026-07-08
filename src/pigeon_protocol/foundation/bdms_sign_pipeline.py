"""bdms a_bogus sign pipeline — static call graph from jsvmp disasm."""
from __future__ import annotations

from typing import Any

from pigeon_protocol.foundation.bdms_jsvmp import JsvmpProgram, load_program
from pigeon_protocol.foundation.bdms_jsvmp_disasm import disasm_function, sign_flow_summary

# Static RE map (bdms v1.0.1.20 jsvmp)
SIGN_PIPELINE: dict[str, Any] = {
    "version": "bdms-1.0.1.20",
    "hook_install": {
        "fn": 104,
        "role": "Patch XMLHttpRequest.prototype open/send/setRequestHeader",
        "hooks": {
            "open": 105,
            "setRequestHeader": 106,
            "send": 107,
        },
    },
    "url_sign_inject": {
        "fn": 107,
        "role": "XHR.send hook — rewrite URL query before native send",
        "flow": [
            "1. If this.bdmsInvokeList: replay queued invoke records",
            "2. Parse URL from args[1] or new URL(location.href)",
            "3. if !searchParams.has('msToken'): append('msToken', closure.inner)",
            "4. if !searchParams.has('a_bogus'): bogus = create(searchParams.toString(), symbol); append('a_bogus', bogus)",
            "5. bdmsInvokeList.forEach(fn#108) — apply deferred func patches",
            "6. delete bdmsInvokeList; apply original send",
        ],
        "a_bogus_call": {
            "pc": "132-166",
            "callee_scope": "depth=2 .create",
            "args": ["url.searchParams.toString()", "symbol(from closure)"],
        },
        "msToken_source": "closure.inner @ depth=2",
    },
    "handleUrl": {
        "fn": 115,
        "defined_by": 113,
        "role": "fetch/EventSource URL normalizer — same msToken/a_bogus append logic",
        "a_bogus_call": {
            "pc": "131-165",
            "callee_scope": "depth=3 .create",
            "args": ["url.search.slice(1)", "{} (new object)"],
        },
    },
    "invoke_replay": {
        "fn": 108,
        "role": "bdmsInvokeList.forEach callback — item.func.apply(item.args)",
    },
    "entries_W": {
        "156": "Runtime bootstrap — bind polyfills/helpers to scope",
        "202": "Main init entry (W(202) in bdms.js) — binds sign helpers",
        "612": "Session/storage fingerprint read path",
        "685": "Shop/device context assembly",
    },
    "next_re": [
        "Trace scope depth-3 `.create` binder — parent of fn#115 (likely W(202) child)",
        "Disasm fn#110/#111 fetch hook (parallel to XHR path)",
        "Port SM3-like `be` class from bdms.js tail into Python (hash input = query slice)",
    ],
}


def pipeline_report(prog: JsvmpProgram | None = None) -> dict[str, Any]:
    if prog is None:
        prog, meta = load_program()
    else:
        meta = {}
    disasm_summary = {}
    for name, idx in (
        ("xhr_send", 107),
        ("handleUrl", 115),
        ("hook_install", 104),
        ("fetch_append", 110),
        ("invoke_cb", 108),
    ):
        if idx < len(prog.functions):
            disasm_summary[name] = {
                "index": idx,
                "sign_flow": sign_flow_summary(prog, idx),
                "disasm_lines": len(disasm_function(prog, idx)["insns"]),
            }
    return {
        "meta": meta,
        "pipeline": SIGN_PIPELINE,
        "disasm_summary": disasm_summary,
    }
