"""Pure-protocol runtime flags — no CDP/browser at call time; Node jsdom bdms is offline OK."""
from __future__ import annotations

import os
from pathlib import Path

from pigeon_protocol.config import LIVE_CAPTURES, REFERENCE_CAPTURES, ROOT

STANDALONE_BUNDLE = Path(os.getenv("PIGEON_BUNDLE_DIR", ROOT / "standalone_bundle"))
BUNDLE_WS_SIGN = STANDALONE_BUNDLE / "ws_sign"
BUNDLE_CONTEXT_BODY = STANDALONE_BUNDLE / "get_by_conversation_body.bin"
BUNDLE_INIT_BODY = STANDALONE_BUNDLE / "get_message_by_init_body.bin"
BUNDLE_WS_INNER = STANDALONE_BUNDLE / "ws_inner_canonical.json"
BUNDLE_WS_INNER_FROM_INIT = STANDALONE_BUNDLE / "ws_inner_from_init.json"
BUNDLE_CONV_SNAPSHOT = STANDALONE_BUNDLE / "conv_sign_snapshot.json"


def refresh_paths() -> None:
    """Reload bundle paths after multi-account switch."""
    global STANDALONE_BUNDLE, BUNDLE_WS_SIGN, BUNDLE_CONTEXT_BODY, BUNDLE_INIT_BODY
    global BUNDLE_WS_INNER, BUNDLE_WS_INNER_FROM_INIT, BUNDLE_CONV_SNAPSHOT
    STANDALONE_BUNDLE = Path(os.getenv("PIGEON_BUNDLE_DIR", ROOT / "standalone_bundle"))
    BUNDLE_WS_SIGN = STANDALONE_BUNDLE / "ws_sign"
    BUNDLE_CONTEXT_BODY = STANDALONE_BUNDLE / "get_by_conversation_body.bin"
    BUNDLE_INIT_BODY = STANDALONE_BUNDLE / "get_message_by_init_body.bin"
    BUNDLE_WS_INNER = STANDALONE_BUNDLE / "ws_inner_canonical.json"
    BUNDLE_WS_INNER_FROM_INIT = STANDALONE_BUNDLE / "ws_inner_from_init.json"
    BUNDLE_CONV_SNAPSHOT = STANDALONE_BUNDLE / "conv_sign_snapshot.json"


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def standalone_mode() -> bool:
    return _env_truthy("PIGEON_STANDALONE")


def pure_only_mode() -> bool:
    """Strict runtime: no CDP/HAR at call time — Node jsdom + Python sign + bundled assets."""
    if _env_truthy("PIGEON_PURE_ONLY"):
        return True
    return standalone_mode()


def node_sign_allowed() -> bool:
    """Node jsdom bdms is offline (no browser); disable with PIGEON_NO_NODE=1."""
    if _env_truthy("PIGEON_NO_NODE"):
        return False
    return True


def prefer_python_abogus() -> bool:
    raw = os.getenv("PIGEON_PREFER_PYTHON_ABOGUS", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    # Default: Node jsdom when available (passes whale 11001); Python is fallback.
    return False


def pigeon_im_needs_sign() -> bool:
    """get_by_conversation works with Cookie + protobuf; no a_bogus required."""
    if _env_truthy("PIGEON_PIGEON_IM_SIGN"):
        return True
    return not pure_only_mode()


def cdp_allowed() -> bool:
    """CDP warm/harvest disabled when PIGEON_NO_CDP=1, PIGEON_PURE_ONLY, or standalone (unless PIGEON_ALLOW_CDP)."""
    if _env_truthy("PIGEON_NO_CDP"):
        return False
    if _env_truthy("PIGEON_PURE_ONLY"):
        return False
    if standalone_mode() and not _env_truthy("PIGEON_ALLOW_CDP"):
        return False
    return True


def bundle_first_assets() -> bool:
    return pure_only_mode() or BUNDLE_CONTEXT_BODY.is_file()


def default_capture_dirs() -> list[Path]:
    dirs = [REFERENCE_CAPTURES, LIVE_CAPTURES]
    if BUNDLE_WS_SIGN.is_dir():
        dirs.append(BUNDLE_WS_SIGN)
    return dirs


def relay_headers_from_hints() -> bool:
    """Use chrome_hints + live CSRF instead of HAR relayHeaders snapshot."""
    return pure_only_mode()
