"""Conversation list via Feige Chrome CDP (bdms + whale verifyFp in-page)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from pigeon_protocol.config import IM_HOST, XUNDAN_QUEUE_KEYS
from pigeon_protocol.conv_list import _unsigned_url, parse_conversation_items
from pigeon_protocol.sign import SIGN_KEYS

logger = logging.getLogger("pigeon.conv_list_cdp")

IM_WORKSPACE = f"{IM_HOST}/pc_seller_v2/main/workspace"

_FETCH_XUNDAN_JS = r"""
async (payload) => {
  const queueKey = payload?.queue_key || "no_pay";
  const pageSize = String(payload?.page_size || 20);

  function whaleV() {
    const ver = (window.gfdatav1 && window.gfdatav1.ver) || "1.0.0.0";
    const parts = ver.split(".");
    const last = parts.pop();
    parts.push(String(parseInt(last || "0", 10) + 1401));
    return parts.join(".");
  }

  function resolveVerifyFp() {
    const ck = document.cookie.match(/s_v_web_id=(verify_[^;]+)/);
    return (ck && ck[1]) || "";
  }

  const verifyFp = resolveVerifyFp();
  const params = new URLSearchParams({
    biz_type: "4",
    PIGEON_BIZ_TYPE: "2",
    _pms: "1",
    device_platform: "web",
    FUSION: "true",
    queue_key: queueKey,
    security_uid_list: "",
    page_size: pageSize,
  });
  if (verifyFp) {
    params.set("verifyFp", verifyFp);
    params.set("fp", verifyFp);
  }

  const url = `https://pigeon.jinritemai.com/backstage/workstation/xundan_chat_list?${params}`;

  const viaXhr = () => new Promise((resolve, reject) => {
    try {
      const xhr = new XMLHttpRequest();
      xhr.open("GET", url, true);
      xhr.withCredentials = true;
      xhr.onload = () => resolve({
        ok: xhr.status >= 200 && xhr.status < 300,
        status: xhr.status,
        finalUrl: xhr.responseURL || url,
        text: (xhr.responseText || "").slice(0, 800000),
        transport: "xhr",
      });
      xhr.onerror = () => reject(new Error("xhr_error"));
      xhr.send();
    } catch (e) {
      reject(e);
    }
  });

  let result;
  try {
    result = await viaXhr();
  } catch (e) {
    const resp = await fetch(url, { method: "GET", credentials: "include" });
    const text = await resp.text();
    result = {
      ok: resp.ok,
      status: resp.status,
      finalUrl: (resp.url || url).slice(0, 4000),
      text: text.slice(0, 800000),
      transport: "fetch",
    };
  }

  return Object.assign(result, {
    whale_v: whaleV(),
    im_pc_version: (window.gfdatav1 && window.gfdatav1.ver) || "",
    verifyFp: verifyFp ? verifyFp.slice(0, 48) : "",
  });
}
"""

_ENSURE_WORKSPACE_JS = r"""
async () => {
  return {
    href: location.href,
    gfdata: (window.gfdatav1 && window.gfdatav1.ver) || "",
    hasBdms: !!window.bdms,
    cookieFp: (document.cookie.match(/s_v_web_id=(verify_[^;]+)/) || [])[1] || "",
    shopId: (document.cookie.match(/SHOP_ID=(\d+)/) || [])[1] || "",
    pigeonCid: (document.cookie.match(/PIGEON_CID=([^;]+)/) || [])[1] || "",
  };
}
"""

_LOGIN_EXPIRED_CODES = frozenset({10005})


def _session_cookies_for_playwright(session) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for name, value in (session.cookies or {}).items():
        if not name or value is None:
            continue
        val = str(value)
        if not val:
            continue
        out.append({"name": str(name), "value": val, "url": f"{IM_HOST}/"})
    return out


def _decode_api_msg(msg: Any) -> str:
    text = str(msg or "")
    if not text:
        return ""
    if any(ord(c) > 127 for c in text):
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def _login_hint(code: Any, msg: Any = "") -> str | None:
    decoded = _decode_api_msg(msg)
    if code in _LOGIN_EXPIRED_CODES or "登录" in decoded:
        return "CDP Chrome 未登录或登录已过期，请在弹出的飞鸽窗口完成登录后重试"
    return None


def _sync_session_from_cdp(session, *, cookies: list[dict], final_url: str) -> list[str]:
    from pigeon_protocol.session import save_session

    applied: list[str] = []
    for c in cookies:
        name = str(c.get("name") or "")
        val = str(c.get("value") or "")
        if name and val and session.cookies.get(name) != val:
            session.cookies[name] = val
            if name == "s_v_web_id":
                session.query_tokens["verifyFp"] = val
                session.query_tokens["fp"] = val
                applied.append("verifyFp")
            if name == "SHOP_ID":
                session.shop_id = val
                applied.append("shop_id")
            if name == "PIGEON_CID":
                session.device_id = val
                applied.append("pigeon_cid")

    if final_url:
        qs = parse_qs(urlparse(final_url).query)
        for key in SIGN_KEYS:
            if qs.get(key):
                session.query_tokens[key] = qs[key][0]
                applied.append(key)
        if qs.get("_v"):
            session.query_tokens["whale_v"] = qs["_v"][0]
            applied.append("whale_v")

    csrf = session.cookies.get("csrf_session_id") or ""
    passport = session.cookies.get("passport_csrf_token") or ""
    if csrf and passport:
        session.headers["x-secsdk-csrf-token"] = f"000100000001{passport},{csrf}"

    try:
        save_session(session)
    except OSError as exc:
        logger.debug("save_session after cdp conv: %s", exc)
    return applied


async def _inject_session_cookies(ctx, session) -> int:
    batch = _session_cookies_for_playwright(session)
    if not batch:
        return 0
    try:
        await ctx.add_cookies(batch)
        return len(batch)
    except Exception as exc:
        logger.warning("session cookie inject failed: %s", exc)
        return 0


async def _wait_for_feige_login(page, *, wait_sec: float) -> dict[str, Any]:
    deadline = time.time() + max(wait_sec, 0)
    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            last = await page.evaluate(_ENSURE_WORKSPACE_JS)
        except Exception as exc:
            if "Execution context was destroyed" in str(exc) or "navigation" in str(exc).lower():
                await page.wait_for_timeout(1500)
                continue
            raise
        if last.get("shopId") and last.get("pigeonCid"):
            last["logged_in"] = True
            return last
        await page.wait_for_timeout(2000)
    try:
        last = await page.evaluate(_ENSURE_WORKSPACE_JS)
    except Exception:
        last = last or {}
    last["logged_in"] = bool(last.get("shopId") and last.get("pigeonCid"))
    return last


def _merge_passive_captures(
    captured_bodies: dict[str, str],
    *,
    merged: list[dict[str, Any]],
    seen: set[str],
    report: dict[str, Any],
) -> None:
    for url, text in captured_bodies.items():
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        items = parse_conversation_items({"data": data})
        qk = parse_qs(urlparse(url).query).get("queue_key", ["passive"])[0]
        for it in items:
            uid = str(it.get("security_user_id") or "")
            if uid and uid in seen:
                continue
            if uid:
                seen.add(uid)
            it["queue_key"] = qk
            merged.append(it)
        report["attempts"].append(
            {
                "passive": True,
                "queue_key": qk,
                "code": data.get("code"),
                "msg": _decode_api_msg(data.get("msg")),
                "items": len(items),
            }
        )


async def _fetch_queues_async(
    session,
    *,
    queue_keys: tuple[str, ...],
    page_size: int,
    port: int,
    timeout_sec: float,
    wait_login_sec: float = 0.0,
    inject_session_cookies: bool = True,
) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import _find_feige_page

    report: dict[str, Any] = {"attempts": [], "items": [], "via": "conv_list/cdp"}
    captured_urls: list[str] = []
    captured_bodies: dict[str, str] = {}
    pending_responses: set[asyncio.Task] = set()

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}",
            timeout=int(timeout_sec * 1000),
        )
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = _find_feige_page(ctx.pages)
        if page is None:
            page = await ctx.new_page()

        async def on_response(resp) -> None:
            u = resp.url or ""
            if "xundan_chat_list" in u and "a_bogus=" in u:
                captured_urls.append(u)
                try:
                    captured_bodies[u] = await resp.text()
                except Exception:
                    pass

        def _schedule_response(resp) -> None:
            task = asyncio.create_task(on_response(resp))
            pending_responses.add(task)
            task.add_done_callback(pending_responses.discard)

        page.on("response", _schedule_response)

        if inject_session_cookies:
            report["cookies_injected"] = await _inject_session_cookies(ctx, session)

        on_workspace = "pc_seller_v2/main/workspace" in (page.url or "")
        if not on_workspace:
            await page.goto(
                IM_WORKSPACE,
                wait_until="domcontentloaded",
                timeout=int(timeout_sec * 1000),
            )
            await page.wait_for_timeout(6000)
        else:
            await page.wait_for_timeout(1500)
        report["warm"] = await page.evaluate(_ENSURE_WORKSPACE_JS)
        report["passive_wait_sec"] = 6

        if pending_responses:
            await asyncio.gather(*pending_responses, return_exceptions=True)

        if report["warm"].get("shopId") and report["warm"].get("pigeonCid"):
            report["warm"]["logged_in"] = True
        elif wait_login_sec > 0:
            report["login_wait_sec"] = wait_login_sec
            report["warm"] = await _wait_for_feige_login(page, wait_sec=wait_login_sec)

        if not report["warm"].get("logged_in"):
            report["ok"] = False
            report["error"] = (
                "CDP Chrome 未登录飞鸽（缺少 SHOP_ID）。"
                "请在 CDP 窗口扫码登录，或运行 python run.py qr-login 刷新 session 后重试。"
            )
            return report

        try:
            from pigeon_protocol.session_sync import CdpSessionSync

            sync = CdpSessionSync(session, port=port, timeout_sec=timeout_sec)
            report["session_sync"] = await sync._sync_async()
        except Exception as exc:
            logger.warning("cdp session sync before xundan: %s", exc)

        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        _merge_passive_captures(captured_bodies, merged=merged, seen=seen, report=report)

        for queue_key in queue_keys:
            try:
                raw = await asyncio.wait_for(
                    page.evaluate(_FETCH_XUNDAN_JS, {"queue_key": queue_key, "page_size": page_size}),
                    timeout=timeout_sec,
                )
            except Exception as exc:
                report["attempts"].append({"queue_key": queue_key, "error": str(exc)})
                continue

            if not isinstance(raw, dict):
                continue
            final_url = str(raw.get("finalUrl") or "")
            if final_url:
                captured_urls.append(final_url)

            text = str(raw.get("text") or "")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {"raw_text": text[:500]}

            code = data.get("code") if isinstance(data, dict) else None
            msg = _decode_api_msg(data.get("msg") if isinstance(data, dict) else "")
            items = parse_conversation_items({"data": data})
            report["attempts"].append(
                {
                    "queue_key": queue_key,
                    "code": code,
                    "msg": msg,
                    "items": len(items),
                    "whale_v": raw.get("whale_v"),
                    "verifyFp": raw.get("verifyFp"),
                    "transport": raw.get("transport"),
                }
            )
            if code in _LOGIN_EXPIRED_CODES and not items:
                report["error"] = _login_hint(code, msg)
                break

            for it in items:
                uid = str(it.get("security_user_id") or "")
                if uid and uid in seen:
                    continue
                if uid:
                    seen.add(uid)
                it["queue_key"] = queue_key
                merged.append(it)

        cookies = await ctx.cookies()
        if captured_urls:
            report["session_applied"] = _sync_session_from_cdp(
                session, cookies=cookies, final_url=captured_urls[-1]
            )
            try:
                from pigeon_protocol.conv_sign_snapshot import save_queue_snapshot
                from pigeon_protocol.order_relay_headers import build_order_relay_headers

                hdr = build_order_relay_headers(session, for_method="GET")
                for url in captured_urls:
                    if "xundan_chat_list" not in url or "a_bogus=" not in url:
                        continue
                    qk = parse_qs(urlparse(url).query).get("queue_key", ["no_order"])[0]
                    save_queue_snapshot(
                        queue_key=qk,
                        url=url,
                        headers=hdr,
                        page_size=page_size,
                        source="conv_list/cdp",
                        unsigned_url=_unsigned_url(queue_key=qk, page_size=page_size, session=session),
                    )
            except Exception as exc:
                logger.debug("cdp conv snapshot save: %s", exc)
        elif merged:
            report["session_applied"] = _sync_session_from_cdp(session, cookies=cookies, final_url="")

        report["items"] = merged
        report["ok"] = bool(merged)
        report["captured_urls"] = len(captured_urls)
        if merged:
            report["data"] = {"code": 0, "data": {"user_list": merged}}
        elif not report.get("error"):
            last = report["attempts"][-1] if report["attempts"] else {}
            report["api_code"] = last.get("code")
            report["error"] = _login_hint(last.get("code"), last.get("msg")) or "cdp xundan returned no items"
        return report


def list_conversations_cdp(
    session,
    *,
    size: int = 30,
    queue_keys: tuple[str, ...] | None = None,
    port: int | None = None,
    timeout_sec: float = 25.0,
    wait_login_sec: float = 90.0,
    auto_launch: bool = True,
    inject_session_cookies: bool = True,
) -> dict[str, Any]:
    from pigeon_protocol.cdp_launch import cdp_port, ensure_cdp_ready

    port = port or cdp_port()
    if not ensure_cdp_ready(launch=auto_launch):
        return {"ok": False, "error": f"CDP not ready on port {port}", "via": "conv_list/cdp"}

    keys = queue_keys or XUNDAN_QUEUE_KEYS
    kwargs = {
        "queue_keys": keys,
        "page_size": size,
        "port": port,
        "timeout_sec": timeout_sec,
        "wait_login_sec": wait_login_sec,
        "inject_session_cookies": inject_session_cookies,
    }
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_fetch_queues_async(session, **kwargs))

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_fetch_queues_async(session, **kwargs))).result(
            timeout=timeout_sec + wait_login_sec + 30
        )
