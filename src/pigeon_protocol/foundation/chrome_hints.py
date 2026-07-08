"""Chrome client hints derived from User-Agent — no HAR snapshot required."""
from __future__ import annotations

import re


def chrome_major_from_ua(user_agent: str) -> str:
    m = re.search(r"Chrome/(\d+)", user_agent or "")
    return m.group(1) if m else "131"


def sec_ch_ua_headers(user_agent: str = "") -> dict[str, str]:
    ver = chrome_major_from_ua(user_agent)
    return {
        "sec-ch-ua": f'"Chromium";v="{ver}", "Google Chrome";v="{ver}", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }


def im_workspace_referer(session, *, page: str = "workspace") -> str:
    from pigeon_protocol.config import IM_HOST

    cid = (
        str(session.cookies.get("PIGEON_CID") or "")
        or str(getattr(session, "device_id", "") or "")
    )
    base = f"{IM_HOST}/pc_seller_v2/main/{page}"
    return f"{base}?selfId={cid}" if cid else base


def backstage_fetch_headers(session, *, method: str = "GET") -> dict[str, str]:
    """Official backstage CORS hints (im.jinritemai.com origin)."""
    from pigeon_protocol.config import DEFAULT_USER_AGENT, IM_HOST

    ua = session.user_agent or DEFAULT_USER_AGENT
    hdr: dict[str, str] = {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Origin": IM_HOST,
        "Referer": im_workspace_referer(session),
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        **sec_ch_ua_headers(ua),
    }
    if method.upper() != "GET":
        hdr["content-type"] = "application/json;charset=UTF-8"
    ck = session.cookie_header()
    if ck:
        hdr["Cookie"] = ck
    return hdr


def pigeon_im_headers(session, *, referer: str | None = None) -> dict[str, str]:
    """Headers for fxg pigeon_im protobuf POST — aligned with official HAR."""
    from pigeon_protocol.config import DEFAULT_USER_AGENT, IM_HOST

    ua = session.user_agent or DEFAULT_USER_AGENT
    hdr: dict[str, str] = {
        "User-Agent": ua,
        "Accept": "application/x-protobuf",
        "Content-Type": "application/x-protobuf",
        "Origin": IM_HOST,
        "Referer": referer or im_workspace_referer(session),
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        **sec_ch_ua_headers(ua),
    }
    ck = session.cookie_header()
    if ck:
        hdr["Cookie"] = ck
    hdr.update(getattr(session, "headers", {}) or {})
    return hdr
