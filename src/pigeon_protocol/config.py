from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STANDALONE_BUNDLE = Path(os.getenv("PIGEON_BUNDLE_DIR", ROOT / "standalone_bundle"))
CAPTURES_DIR = Path(os.getenv("PIGEON_CAPTURES_DIR", ROOT / "captures"))
REFERENCE_CAPTURES = CAPTURES_DIR / "reference"
LIVE_CAPTURES = CAPTURES_DIR / "live"
SESSION_DIR = Path(os.getenv("PIGEON_SESSION_DIR", ROOT / "session"))
SESSION_FILE = SESSION_DIR / "session.json"
LOGS_DIR = Path(os.getenv("PIGEON_LOGS_DIR", ROOT / "logs"))


def refresh_paths() -> None:
    """Reload path constants after PIGEON_* env changes (multi-account switch)."""
    global STANDALONE_BUNDLE, SESSION_DIR, SESSION_FILE, LOGS_DIR
    STANDALONE_BUNDLE = Path(os.getenv("PIGEON_BUNDLE_DIR", ROOT / "standalone_bundle"))
    SESSION_DIR = Path(os.getenv("PIGEON_SESSION_DIR", ROOT / "session"))
    SESSION_FILE = SESSION_DIR / "session.json"
    LOGS_DIR = Path(os.getenv("PIGEON_LOGS_DIR", ROOT / "logs"))

PIGEON_HOST = "https://pigeon.jinritemai.com"
IM_HOST = "https://im.jinritemai.com"
FEIGE_URL = "https://im.jinritemai.com/pc_seller_v2/main"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

WS_HOST_HINTS = (
    "ws.fxg.jinritemai.com",
    "frontier.snssdk.com",
)

HTTP_INBOUND_HINTS = (
    "get_history_msg",
    "fuzzySearchConversation",
    "xundan_chat_list",
    "msg_body",
    "get_by_conversation",
    "message/list",
)

ORDER_QUERY_PATH = "/backstage/cmpoent/order/query"
HISTORY_MSG_PATH = "/backstage/get_history_msg_sub"
CONV_LIST_PATH = "/backstage/fuzzySearchConversation"
XUNDAN_CHAT_LIST_PATH = "/backstage/workstation/xundan_chat_list"
GET_LINK_INFO_PATH = "/chat/api/backstage/conversation/get_link_info"
CURRENT_CONV_LIST_PATH = "/chat/api/backstage/conversation/get_current_conversation_list"
USER_CARD_PATH = "/backstage/workstation/get_user_card"

# xundan 工作台队列 — 最近联系等多 tab
XUNDAN_QUEUE_KEYS = ("no_order", "no_pay", "pending", "after_sale", "all")


@dataclass
class AppConfig:
    dry_run: bool = True
    listen_timeout_sec: int = 120
    http_timeout_sec: float = 15.0
    ws_ping_interval_sec: int = 25
    capture_dirs: list[Path] = field(
        default_factory=lambda: __import__(
            "pigeon_protocol.pure_config", fromlist=["default_capture_dirs"]
        ).default_capture_dirs()
    )
