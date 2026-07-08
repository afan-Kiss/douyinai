#!/usr/bin/env python3
"""Export Feige Electron Pigeon Rust SDK artifacts into analysis/feige_electron_sdk/."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEIGE = Path("E:/feige-electron/抖店工作台/1.1.7")
FEIGE_EXTRACTED = Path("E:/feige-electron/extracted-app")

COPY_FILES = [
    (
        FEIGE_EXTRACTED / "node_modules/@pigeon-sdk/rust-sdk-api/src/index.js",
        "rust-sdk-api/index.js",
    ),
]

COPY_PATHS = [
    FEIGE / "resources/app.asar.unpacked/node_modules/@pigeon-sdk/rust-sdk",
    FEIGE / "resources/app.asar.unpacked/node_modules/@pigeon-sdk/rust-sdk-win32-x64-msvc",
    FEIGE / "resources/app.asar.unpacked/node_modules/@pigeon-sdk/rust-sdk-win32-ia32-msvc",
    FEIGE_EXTRACTED / "node_modules/@pigeon-sdk/rust-sdk-engine-electron/dist",
]

OUT = ROOT / "analysis" / "feige_electron_sdk"


def copy_node_deps() -> list[str]:
    """Copy protobufjs + long for offline Node invoke (no Feige install at runtime)."""
    exported: list[str] = []
    src_nm = FEIGE_EXTRACTED / "node_modules"
    dst_nm = OUT / "node_modules"
    if not src_nm.is_dir():
        return exported
    for pkg in ("protobufjs", "long", "@protobufjs"):
        src = src_nm / pkg
        if not src.exists():
            continue
        dst = dst_nm / pkg
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst)
        if src.is_dir():
            shutil.copytree(src, dst)
            exported.append(pkg)
    return exported


def copy_tree(src: Path, dst: Path) -> None:
    if not src.is_dir():
        return
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def scan_strings(path: Path, needles: tuple[bytes, ...], limit: int = 40) -> list[dict]:
    try:
        data = path.read_bytes()
    except OSError:
        return []
    hits: list[dict] = []
    for n in needles:
        idx = 0
        while True:
            idx = data.find(n, idx)
            if idx == -1:
                break
            ctx = data[max(0, idx - 24) : idx + len(n) + 48]
            hits.append(
                {
                    "needle": n.decode("ascii", errors="ignore"),
                    "offset": idx,
                    "context": ctx.decode("ascii", errors="replace")[:120],
                }
            )
            idx += len(n)
            if len(hits) >= limit:
                return hits
    return hits


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    exported: list[str] = []
    for src in COPY_PATHS:
        if not src.exists():
            continue
        if src.name == "dist":
            rel = "rust-sdk-engine-electron"
        elif src.parent.name == "@pigeon-sdk":
            rel = src.name
        else:
            rel = src.name
        dst = OUT / rel
        copy_tree(src, dst)
        exported.append(rel)

    for src, rel in COPY_FILES:
        if not src.is_file():
            continue
        dst = OUT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        exported.append(rel)

    node_deps = copy_node_deps()
    if node_deps:
        exported.append(f"node_modules:{','.join(node_deps)}")

    node = FEIGE / "resources/app.asar.unpacked/node_modules/@pigeon-sdk/rust-sdk-win32-x64-msvc/rust-sdk.win32-x64-msvc.node"
    if node.is_file():
        rust_sdk_node = OUT / "rust-sdk" / "rust-sdk.win32-x64-msvc.node"
        rust_sdk_node.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(node, rust_sdk_node)
        exported.append("rust-sdk/rust-sdk.win32-x64-msvc.node")
    strings = scan_strings(
        node,
        (
            b"packedMessage",
            b"PigeonIMCreateMessage",
            b"invokeAsync",
            b"initSdkFromBuffer",
            b"createClient",
        ),
    )

    report = {
        "feige_root": str(FEIGE),
        "exported": exported,
        "native_node": str(node),
        "native_size": node.stat().st_size if node.is_file() else 0,
        "exports_from_index_js": [
            "sum",
            "initSdkFromBuffer",
            "initSdk",
            "createClient",
            "removeClient",
            "invokeAsync",
            "getDevice",
        ],
        "cmd_create_message": 11327,
        "im_create_message_fields": [
            "conversation_id",
            "type",
            "content",
            "client_message_id",
            "ext",
        ],
        "bridge": "webviewBridge.getSDKClient() via rust-sdk-engine-electron preload",
        "invoke_flow": [
            "initSdkFromBuffer(InitSDKReq.encode) — not initSdk(object)",
            "createClient(push_cb) -> clientId",
            "invokeAsync(clientId, packed_pb, cmd=11327) -> 169B inner in response",
        ],
        "pure_protocol_note": "Runtime uses analysis/feige_electron_sdk only; no Feige client/browser",
        "string_hits": strings[:30],
        "ok": bool(exported) and node.is_file(),
    }
    (OUT / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
