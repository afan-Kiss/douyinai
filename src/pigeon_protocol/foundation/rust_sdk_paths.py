"""Resolve bundled Pigeon Rust SDK paths — offline RE assets, no Feige client at runtime."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SDK_ROOT = ROOT / "analysis" / "feige_electron_sdk"


def rust_sdk_layout() -> dict[str, Any]:
    """Return paths for native .node, API js, and NODE_PATH for protobufjs."""
    env_native = os.environ.get("PIGEON_RUST_SDK_NATIVE", "").strip()
    env_api = os.environ.get("PIGEON_RUST_SDK_API", "").strip()
    env_nm = os.environ.get("PIGEON_RUST_SDK_NODE_MODULES", "").strip()

    native_pkg = Path(env_native) if env_native else SDK_ROOT / "rust-sdk-win32-x64-msvc"
    api_js = Path(env_api) if env_api else SDK_ROOT / "rust-sdk-api" / "index.js"
    node_modules = Path(env_nm) if env_nm else SDK_ROOT / "node_modules"

    node_file = native_pkg / "rust-sdk.win32-x64-msvc.node"
    rust_sdk_dir = SDK_ROOT / "rust-sdk"

    ok = node_file.is_file() and api_js.is_file()
    return {
        "ok": ok,
        "root": str(SDK_ROOT),
        "native_pkg": str(native_pkg),
        "node_file": str(node_file),
        "rust_sdk_dir": str(rust_sdk_dir),
        "api_js": str(api_js),
        "node_modules": str(node_modules),
        "node_exists": node_file.is_file(),
        "api_exists": api_js.is_file(),
    }
