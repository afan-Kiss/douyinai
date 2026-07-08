"""Shared datatypes for the protocol foundation layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BdmsSignResult:
    ok: bool
    signed_url: str
    tokens: dict[str, str] = field(default_factory=dict)
    via: str = "node_bdms"
    method: str = "GET"
    raw: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class RelayResponse:
    ok: bool
    status: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    via: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error: str = ""
    sign: BdmsSignResult | None = None

    def api_code(self) -> str:
        inner = self.data.get("data") if isinstance(self.data.get("data"), dict) else self.data
        if not isinstance(inner, dict):
            inner = self.data
        for key in ("code", "st"):
            val = inner.get(key) if isinstance(inner, dict) else None
            if val is not None:
                return str(val)
        return ""

    def api_ok(self) -> bool:
        code = self.api_code()
        if code in ("11001", "10009", "10001010A"):
            return False
        return code in ("0", "200") or (self.ok and self.status == 200 and code == "")


@dataclass
class WsSendCapability:
    strategy: str
    ready: bool
    template_lengths: list[int] = field(default_factory=list)
    bucket_count: int = 0
    computed_blob: bool = False
    missing_lengths: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class FoundationReport:
    ok: bool
    bdms_node: bool
    curl_cffi: bool
    relay_headers: bool
    session_cookies: int
    ws_urls: int
    ws_send: WsSendCapability
    http_sign: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    re_targets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "bdms_node": self.bdms_node,
            "curl_cffi": self.curl_cffi,
            "relay_headers": self.relay_headers,
            "session_cookies": self.session_cookies,
            "ws_urls": self.ws_urls,
            "ws_send": {
                "strategy": self.ws_send.strategy,
                "ready": self.ws_send.ready,
                "template_lengths": self.ws_send.template_lengths,
                "bucket_count": self.ws_send.bucket_count,
                "computed_blob": self.ws_send.computed_blob,
                "missing_lengths": self.ws_send.missing_lengths,
                "notes": self.ws_send.notes,
            },
            "http_sign": self.http_sign,
            "blockers": self.blockers,
            "re_targets": self.re_targets,
        }
