from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pigeon_protocol.config import DEFAULT_USER_AGENT


def _default_session_file() -> Path:
    from pigeon_protocol.account_context import session_file

    return session_file()


@dataclass
class SessionState:
    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    query_tokens: dict[str, str] = field(default_factory=dict)
    ws_urls: list[str] = field(default_factory=list)
    device_id: str = ""
    shop_id: str = ""
    user_agent: str = DEFAULT_USER_AGENT
    source_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items() if v)

    def merge_query_tokens(self, url: str) -> None:
        try:
            qs = parse_qs(urlparse(url).query)
        except Exception:
            return
        for key in ("verifyFp", "fp", "msToken", "a_bogus", "token", "access_key", "device_id", "pigeon_sign"):
            if qs.get(key):
                self.query_tokens[key] = qs[key][0]
        if qs.get("device_id") and not self.device_id:
            self.device_id = qs["device_id"][0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cookies": self.cookies,
            "headers": self.headers,
            "query_tokens": self.query_tokens,
            "ws_urls": self.ws_urls,
            "device_id": self.device_id,
            "shop_id": self.shop_id,
            "user_agent": self.user_agent,
            "source_files": self.source_files,
            "notes": self.notes,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionState:
        return cls(
            cookies=dict(data.get("cookies") or {}),
            headers=dict(data.get("headers") or {}),
            query_tokens=dict(data.get("query_tokens") or {}),
            ws_urls=list(data.get("ws_urls") or []),
            device_id=str(data.get("device_id") or ""),
            shop_id=str(data.get("shop_id") or ""),
            user_agent=str(data.get("user_agent") or DEFAULT_USER_AGENT),
            source_files=list(data.get("source_files") or []),
            notes=list(data.get("notes") or []),
            extra=dict(data.get("extra") or {}),
        )


def load_session(path: Path | None = None) -> SessionState:
    target = path or _default_session_file()
    if not target.exists():
        return SessionState(notes=["session file missing — run extract-session first"])
    data = json.loads(target.read_text(encoding="utf-8"))
    return SessionState.from_dict(data)


def save_session(session: SessionState, path: Path | None = None) -> Path:
    target = path or _default_session_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def extract_session_from_capture_event(session: SessionState, event: dict[str, Any], source: str) -> None:
    if source not in session.source_files:
        session.source_files.append(source)

    url = str(event.get("url") or "")
    if url:
        session.merge_query_tokens(url)
        if url.startswith("wss://") and url not in session.ws_urls:
            session.ws_urls.append(url)

    headers = event.get("headers") or {}
    if isinstance(headers, dict):
        for key in ("User-Agent", "user-agent", "x-secsdk-csrf-token", "Referer", "Origin"):
            val = headers.get(key) or headers.get(key.lower())
            if val:
                session.headers[key if key != "user-agent" else "User-Agent"] = str(val)
        cookie_raw = headers.get("Cookie") or headers.get("cookie")
        if cookie_raw:
            for part in str(cookie_raw).split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    session.cookies[k.strip()] = v.strip()

    post_data = event.get("post_data")
    if isinstance(post_data, str) and post_data.startswith("{"):
        try:
            body = json.loads(post_data)
            uid = str(body.get("security_user_id") or "")
            if uid.startswith("AQ"):
                session.notes.append(f"seen security_user_id={uid[:12]}...")
        except json.JSONDecodeError:
            pass

    resp = str(event.get("response_body") or "")
    shop_match = re.search(r'"shop_id"\s*:\s*"(\d+)"', resp)
    if shop_match and not session.shop_id:
        session.shop_id = shop_match.group(1)


BACKSTAGE_SIGN_KEYS = ("verifyFp", "fp", "msToken", "a_bogus")
WS_SIGN_KEYS = ("token", "access_key", "device_id", "pigeon_sign")


def build_signed_url(base_url: str, session: SessionState, extra: dict[str, str] | None = None) -> str:
    from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

    parsed = urlparse(base_url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key in BACKSTAGE_SIGN_KEYS:
        if session.query_tokens.get(key):
            params[key] = session.query_tokens[key]
    if extra:
        params.update({k: v for k, v in extra.items() if v})
    query = urlencode(params)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment))
