"""Automated WS send-template harvesting via CDP (bootstrap only, not runtime send)."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pigeon_protocol.capture_loader import index_send_templates, list_send_template_pool
from pigeon_protocol.config import LIVE_CAPTURES
from pigeon_protocol.ws_sign_import import import_sample, sample_to_event

logger = logging.getLogger("pigeon.harvest")

WORKSPACE_URL = "https://im.jinritemai.com/pc_seller_v2/main/workspace"
# Feige textarea truncates UI sends at ~120B unless maxlength bypassed; 121+ may still need RE.
FEIGE_UI_MAX_TEXT_BYTES = 200

# Common UTF-8 text byte lengths for customer-service replies
QUICK_LADDER = (6, 9, 12, 15, 18, 21, 24, 30, 45, 60, 77, 78, 90, 120)
DEFAULT_LADDER = (
    3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45,
    48, 51, 54, 57, 60, 63, 66, 69, 72, 75, 77, 78, 81, 84, 90, 96, 120, 150, 200,
)
# textB > 200 — Feige UI allows long replies when maxlength is bypassed (harvest only).
LONG_MESSAGE_LADDER = (201, 210, 220, 250, 280, 300, 350, 400, 500)

INSTALL_CAPTURE_JS = r"""
() => {
  if (!window.__wsSignCapture) window.__wsSignCapture = { samples: [], patched: false };

  const captureSend = (ws, data) => {
    try {
      let bytes;
      if (data instanceof ArrayBuffer) bytes = new Uint8Array(data);
      else if (data instanceof Uint8Array) bytes = data;
      else if (typeof data === "string") bytes = new TextEncoder().encode(data);
      else return;
      if (bytes.length >= 2800 && bytes.length < 12000) {
        let s = "";
        for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
        if (s.includes("s:client_message_id")) {
          window.__wsSignCapture.samples.push({
            t: Date.now(), len: bytes.length, b64: btoa(s),
            url: (ws.url || "").slice(0, 200),
          });
        }
      }
    } catch (e) {}
  };

  if (!window.__pigeonWsCapture) {
    window.__pigeonWsCapture = { ws: null, url: "" };
    const NativeWS = WebSocket;
    function track(ws, url) {
      if (String(url || ws.url || "").includes("ws.fxg.jinritemai.com")) {
        window.__pigeonWsCapture.ws = ws;
        window.__pigeonWsCapture.url = ws.url || String(url);
      }
    }
    WebSocket = function(url, protocols) {
      const ws = protocols !== undefined ? new NativeWS(url, protocols) : new NativeWS(url);
      track(ws, url);
      return ws;
    };
    WebSocket.prototype = NativeWS.prototype;
    WebSocket.CONNECTING = NativeWS.CONNECTING;
    WebSocket.OPEN = NativeWS.OPEN;
    WebSocket.CLOSING = NativeWS.CLOSING;
    WebSocket.CLOSED = NativeWS.CLOSED;
    const nativeSend = NativeWS.prototype.send;
    NativeWS.prototype.send = function(data) {
      track(this, this.url);
      captureSend(this, data);
      return nativeSend.apply(this, arguments);
    };
    window.__wsSignCapture.patched = true;
  } else if (!window.__wsSignCapture.patched) {
    const ws = window.__pigeonWsCapture?.ws;
    if (ws && !ws.__signPatched) {
      const orig = ws.send.bind(ws);
      ws.send = function(data) {
        captureSend(ws, data);
        return orig(data);
      };
      ws.__signPatched = true;
      window.__wsSignCapture.patched = true;
    }
  }

  const cap = window.__pigeonWsCapture;
  const ws = cap?.ws;
  return {
    ok: !!window.__wsSignCapture.patched || !!window.__pigeonWsCapture,
    ws_url: ws ? (ws.url || "").slice(0, 120) : "",
    n: window.__wsSignCapture.samples.length,
    state: ws ? ws.readyState : -1,
    patched: window.__wsSignCapture.patched,
    has_ws: !!ws,
  };
}
"""

_WS_STATUS_JS = r"""
() => {
  const ws = window.__pigeonWsCapture?.ws;
  if (!ws) return { ok: false, has_ws: false, state: -1 };
  return { ok: ws.readyState === 1, has_ws: true, state: ws.readyState, url: (ws.url || "").slice(0, 120) };
}
"""

SEND_UI_JS = r"""
async (text) => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 60 && r.height > 16 && r.bottom > 0 && r.right > 0;
  };
  const setText = (editor, value) => {
    if (editor.tagName === "TEXTAREA") {
      try { editor.removeAttribute("maxlength"); editor.maxLength = 100000; } catch (e) {}
      const proto = Object.getPrototypeOf(editor);
      const desc = Object.getOwnPropertyDescriptor(proto, "value");
      if (desc && desc.set) desc.set.call(editor, value);
      else editor.value = value;
      editor.dispatchEvent(new Event("input", { bubbles: true }));
      editor.dispatchEvent(new Event("change", { bubbles: true }));
      return { mode: "textarea_native_set", len: (editor.value || "").length };
    }
    editor.textContent = value;
    editor.dispatchEvent(new InputEvent("input", { bubbles: true, data: value }));
    return { mode: "contenteditable", len: (editor.textContent || "").length };
  };
  const textareas = [...document.querySelectorAll("textarea")].filter(visible);
  const editables = [...document.querySelectorAll('[contenteditable="true"]')].filter(visible);
  let editor = textareas.find(el => /inputArea|发送/i.test(el.className + el.placeholder)) || textareas[0] || editables[0];
  if (!editor) return { ok: false, error: "no_editor", textareas: textareas.length, editables: editables.length };
  editor.focus();
  const setInfo = setText(editor, text);
  await sleep(300);
  const buttons = [...document.querySelectorAll("button, [role=button], span, div")]
    .filter(el => /发送|send/i.test((el.textContent || "").trim()) && visible(el));
  if (buttons[0]) {
    buttons[0].click();
    return { ok: true, mode: "button_click", tag: editor.tagName, ...setInfo };
  }
  editor.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true }));
  editor.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true }));
  return { ok: true, mode: "enter_key", tag: editor.tagName, ...setInfo };
}
"""

POLL_SAMPLES_JS = "() => window.__wsSignCapture?.samples || []"

DEFAULT_HARVEST_UID = (
    "AQCnSRsg6VjCVV6CzwN4oOAcHF9PP0l8Wt61aPf6eWv91CiWTitMouMi93A9JW_hl54iRJnOiiFe7Sfrh83xb6Nk"
)

_OPEN_CHAT_JS = r"""
async (uid) => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const hasEditor = () => {
    for (const el of document.querySelectorAll('textarea[class*="inputArea"], textarea, [contenteditable="true"]')) {
      const r = el.getBoundingClientRect();
      if (r.width > 100 && r.height > 20) return true;
    }
    return false;
  };
  if (hasEditor()) return { ok: true, mode: "already" };

  const frag = String(uid || "").slice(0, 32);
  const short = frag.slice(0, 24);

  for (const sel of [`[class*="${short}"]`, `[class*="${frag.slice(0, 16)}"]`]) {
    for (const el of document.querySelectorAll(sel)) {
      const r = el.getBoundingClientRect();
      if (r.width < 40 || r.height < 20) continue;
      el.click();
      await sleep(1200);
      if (hasEditor()) return { ok: true, mode: "uid_class", sel };
    }
  }

  const rows = [...document.querySelectorAll("div, li, span")]
    .filter(el => {
      const r = el.getBoundingClientRect();
      const t = (el.textContent || "").trim();
      return r.x < 480 && r.width > 120 && r.height > 36 && r.height < 130
        && t.length > 1 && t.length < 80 && !/设置|搜索|全部|待回复|系统|工作台|飞鸽/.test(t);
    });
  for (const row of rows.slice(0, 12)) {
    row.click();
    await sleep(1200);
    if (hasEditor()) return { ok: true, mode: "row_click", text: (row.textContent || "").trim().slice(0, 40) };
  }

  return { ok: hasEditor(), mode: hasEditor() ? "late" : "failed", rows: rows.length };
}
"""


async def _navigate_workspace(page) -> dict[str, Any]:
    """Ensure Feige is on workspace view (conversation list + chat panel)."""
    url = page.url or ""
    if "/workspace" in url:
        await page.wait_for_timeout(1500)
        return {"ok": True, "mode": "already_workspace", "url": url[:120]}

    # SPA menu: click 工作台 / 会话 if visible
    for label in ("工作台", "会话", "消息"):
        try:
            btn = page.get_by_text(label, exact=False).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=3000)
                await page.wait_for_timeout(2500)
                if "/workspace" in (page.url or ""):
                    return {"ok": True, "mode": "menu_click", "label": label}
        except Exception:
            continue

    try:
        await page.goto(WORKSPACE_URL, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)
        return {"ok": "/workspace" in (page.url or ""), "mode": "goto", "url": (page.url or "")[:120]}
    except Exception as exc:
        return {"ok": False, "mode": "goto_failed", "error": str(exc)[:200]}


async def _ensure_chat_open(page, *, uid: str = DEFAULT_HARVEST_UID) -> dict[str, Any]:
    """Open buyer chat via Playwright + JS fallback."""
    nav = await _navigate_workspace(page)
    if not nav.get("ok"):
        logger.warning("workspace navigation: %s", nav)

    loc = page.locator('textarea[class*="inputArea"], textarea').first
    try:
        if await loc.count() > 0 and await loc.is_visible():
            return {"ok": True, "mode": "playwright_editor", "nav": nav}
    except Exception:
        pass

    # Click first conversation row in left panel (Feige 2026 UI)
    for sel in (
        '[class*="conversation"] [class*="item"]',
        '[class*="session"] [class*="item"]',
        '[class*="SessionItem"]',
        '[class*="ConvItem"]',
        '[class*="chat-item"]',
        '[class*="list"] [class*="item"]',
    ):
        items = page.locator(sel)
        try:
            n = await items.count()
            for i in range(min(n, 5)):
                await items.nth(i).click(timeout=4000)
                await page.wait_for_timeout(1500)
                try:
                    if await loc.count() > 0 and await loc.is_visible():
                        return {"ok": True, "mode": "playwright_conv", "sel": sel, "idx": i, "nav": nav}
                except Exception:
                    continue
        except Exception:
            continue

    js_result = await page.evaluate(_OPEN_CHAT_JS, uid)
    js_result["nav"] = nav
    return js_result


async def _ensure_ws_connected(page, *, timeout_sec: float = 45.0) -> dict[str, Any]:
    """Wait for Feige WS; reload once if needed (then re-open chat)."""
    from pigeon_protocol.cdp_ws import _WS_HOOK_INSTALL_JS

    await page.evaluate(_WS_HOOK_INSTALL_JS)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        st = await page.evaluate(_WS_STATUS_JS)
        if st.get("state") == 1:
            return st
        await asyncio.sleep(0.8)

    logger.info("WS not open — reloading workspace once")
    try:
        await page.reload(wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(5000)
        await _ensure_chat_open(page)
        await page.evaluate(_WS_HOOK_INSTALL_JS)
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            st = await page.evaluate(_WS_STATUS_JS)
            if st.get("state") == 1:
                return st
            await asyncio.sleep(0.8)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": False, "error": "ws_timeout"}


def text_for_byte_length(byte_len: int) -> str:
    """Build filler text with exact UTF-8 byte length (for template harvesting)."""
    if byte_len <= 0:
        return ""
    parts: list[str] = []
    remain = byte_len
    while remain >= 3:
        parts.append("好")
        remain -= 3
    if remain == 1:
        parts.append("1")
    elif remain == 2:
        parts.append("12")
    return "".join(parts)


def missing_lengths(lengths: list[int] | tuple[int, ...] | None = None) -> list[int]:
    pool = index_send_templates()
    want = list(lengths or DEFAULT_LADDER)
    return [n for n in want if n not in pool]


def ensure_template_sync(byte_len: int, *, timeout_sec: float = 20.0) -> bool:
    if byte_len in index_send_templates():
        return True
    try:
        return asyncio.run(harvest_lengths([byte_len], timeout_sec=timeout_sec)) > 0
    except Exception as exc:
        logger.warning("ensure_template_sync failed: %s", exc)
        return False


async def _send_via_ui(page, text: str) -> dict[str, Any]:
    """Playwright-native send (more reliable than synthetic DOM events)."""
    loc = page.locator('textarea[class*="inputArea"], textarea').first
    try:
        if await loc.count() == 0:
            return await page.evaluate(SEND_UI_JS, text)
        await loc.click(timeout=3000)
        await loc.evaluate(
            """(el, value) => {
              try { el.removeAttribute('maxlength'); el.maxLength = 100000; } catch (e) {}
              const proto = Object.getPrototypeOf(el);
              const desc = Object.getOwnPropertyDescriptor(proto, 'value');
              if (desc && desc.set) desc.set.call(el, value);
              else el.value = value;
              el.dispatchEvent(new Event('input', { bubbles: true }));
            }""",
            text,
        )
        actual = await loc.input_value()
        await loc.press("Enter", timeout=3000)
        return {"ok": True, "mode": "playwright_native_set", "editor_len": len(actual.encode("utf-8"))}
    except Exception as exc:
        fallback = await page.evaluate(SEND_UI_JS, text)
        fallback["playwright_error"] = str(exc)[:200]
        return fallback


async def harvest_lengths(
    lengths: list[int],
    *,
    port: int = 9222,
    timeout_sec: float = 20.0,
    delay_sec: float = 1.2,
    allow_long: bool = False,
) -> int:
    from playwright.async_api import async_playwright

    from pigeon_protocol.cdp_bridge import cdp_ready

    if not cdp_ready(port):
        logger.error("CDP not ready on port %s", port)
        return 0
    if not lengths:
        return 0

    harvested = 0
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}",
            timeout=int(timeout_sec * 1000),
        )
        page = next((pg for pg in browser.contexts[0].pages if "jinritemai" in (pg.url or "")), None)
        if page is None:
            logger.error("No Feige page — open im.jinritemai.com and select a buyer chat")
            return 0

        # Try to open a buyer chat if editor not visible (workspace-only view)
        open_r = await _ensure_chat_open(page)
        if not open_r.get("ok"):
            logger.warning("Could not open chat automatically: %s — click a buyer in Feige", open_r)
        else:
            logger.info("Chat ready: %s", open_r)

        ws_st = await _ensure_ws_connected(page)
        if ws_st.get("state") != 1:
            logger.error("WS not connected: %s", ws_st)
            return 0

        hook = await page.evaluate(INSTALL_CAPTURE_JS)
        if not hook.get("ok"):
            logger.error("WS hook failed: %s", hook)
            return 0
        logger.info("WS hook ready: %s", hook)

        for byte_len in lengths:
            if byte_len in index_send_templates():
                continue
            if not allow_long and byte_len > FEIGE_UI_MAX_TEXT_BYTES:
                logger.info("skip %sB (> Feige UI max %s; use allow_long=True)", byte_len, FEIGE_UI_MAX_TEXT_BYTES)
                continue
            text = text_for_byte_length(byte_len)
            actual = len(text.encode("utf-8"))
            if actual != byte_len:
                logger.warning("text_for_byte_length(%s) got %s bytes", byte_len, actual)
                continue

            # Re-ensure chat if editor disappeared between sends
            open_r = await _ensure_chat_open(page)
            if not open_r.get("ok"):
                logger.warning("lost chat editor before %sB: %s", byte_len, open_r)
                continue

            before = await page.evaluate(POLL_SAMPLES_JS)
            before_n = len(before) if isinstance(before, list) else 0
            send_result = await _send_via_ui(page, text)
            if not send_result.get("ok"):
                logger.warning("UI send failed for %sB: %s", byte_len, send_result)
                continue

            deadline = time.time() + timeout_sec
            new_sample: dict[str, Any] | None = None
            while time.time() < deadline:
                await asyncio.sleep(0.4)
                samples = await page.evaluate(POLL_SAMPLES_JS)
                if not isinstance(samples, list) or len(samples) <= before_n:
                    continue
                for raw in samples[before_n:]:
                    event = sample_to_event(raw)
                    if event.get("text_byte_length") == byte_len:
                        new_sample = {**raw, **event}
                        break
                if new_sample:
                    break
                # fallback: latest frame if only one new sample
                if len(samples) == before_n + 1:
                    event = sample_to_event(samples[-1])
                    if event.get("text_byte_length") == byte_len:
                        new_sample = {**samples[-1], **event}
                        break

            if not new_sample:
                logger.warning("no WS capture for %sB text=%r", byte_len, text[:20])
                continue

            cap_len = new_sample.get("text_byte_length")
            if cap_len != byte_len:
                logger.warning(
                    "WS capture length mismatch for %sB: got %s hint=%r",
                    byte_len,
                    cap_len,
                    (new_sample.get("text_hint") or [""])[0][:30],
                )
                continue

            new_sample["source"] = "auto_harvest"
            path = import_sample(new_sample)
            # rename to stable byte-length key
            stable = LIVE_CAPTURES / "ws_sign" / f"live_ws_frame_sent_b{byte_len:03d}.json"
            if path != stable:
                stable.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                if path.name != stable.name:
                    try:
                        path.unlink()
                    except OSError:
                        pass
            harvested += 1
            logger.info("harvested %sB -> %s frame=%s", byte_len, stable.name, new_sample.get("len"))
            await asyncio.sleep(delay_sec)

    return harvested


async def bootstrap_templates(
    *,
    lengths: list[int] | None = None,
    port: int = 9222,
) -> dict[str, Any]:
    """Harvest all missing lengths from ladder (CDP + Feige UI, one-time bootstrap)."""
    want = list(lengths or DEFAULT_LADDER)
    missing = missing_lengths(want)
    report: dict[str, Any] = {
        "requested": want,
        "missing_before": missing,
        "harvested": 0,
        "pool_after": [],
    }
    if missing:
        report["harvested"] = await harvest_lengths(missing, port=port)
    report["pool_after"] = list_send_template_pool()
    report["still_missing"] = missing_lengths(want)
    return report


def bootstrap_templates_sync(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(bootstrap_templates(**kwargs))


async def bootstrap_long_templates(
    *,
    lengths: list[int] | None = None,
    port: int = 9222,
    timeout_sec: float = 30.0,
    delay_sec: float = 2.0,
) -> dict[str, Any]:
    """Harvest textB > 200 via CDP (one-time bootstrap for long replies)."""
    want = list(lengths or LONG_MESSAGE_LADDER)
    missing = missing_lengths(want)
    report: dict[str, Any] = {
        "requested": want,
        "missing_before": missing,
        "harvested": 0,
        "pool_after": [],
        "allow_long": True,
    }
    if missing:
        report["harvested"] = await harvest_lengths(
            missing,
            port=port,
            timeout_sec=timeout_sec,
            delay_sec=delay_sec,
            allow_long=True,
        )
    report["pool_after"] = list_send_template_pool()
    report["still_missing"] = missing_lengths(want)
    return report


def bootstrap_long_templates_sync(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(bootstrap_long_templates(**kwargs))
