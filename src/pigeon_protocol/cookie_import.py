"""Import cookies into session.json without CDP."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pigeon_protocol.session import SessionState, load_session, save_session


def parse_cookie_header(raw: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in str(raw or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def parse_netscape_file(text: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            cookies[parts[5]] = parts[6]
    return cookies


def parse_cookie_file(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            if "cookies" in data and isinstance(data["cookies"], dict):
                return {str(k): str(v) for k, v in data["cookies"].items()}
            if all(isinstance(v, str) for v in data.values()):
                return {str(k): str(v) for k, v in data.items()}
        if isinstance(data, list):
            out: dict[str, str] = {}
            for item in data:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("key")
                value = item.get("value")
                if name and value is not None:
                    out[str(name)] = str(value)
            return out
        raise ValueError("unsupported JSON cookie format")
    if "\t" in text and re.search(r"^\.", text, re.M):
        return parse_netscape_file(text)
    if "=" in text and ";" in text:
        return parse_cookie_header(text)
    raise ValueError("unrecognized cookie file format")


def import_cookies(
    source: str | Path,
    *,
    merge: bool = True,
    shop_id: str = "",
    user_agent: str = "",
) -> SessionState:
    path = Path(source)
    if path.exists():
        cookies = parse_cookie_file(path)
        source_label = str(path)
    else:
        cookies = parse_cookie_header(str(source))
        source_label = "inline"

    session = load_session() if merge else SessionState()
    session.cookies.update(cookies)
    if shop_id:
        session.shop_id = shop_id
    if user_agent:
        session.user_agent = user_agent
    note = f"imported cookies from {source_label} ({len(cookies)} keys)"
    if note not in session.notes:
        session.notes.append(note)
    save_session(session)
    return session
