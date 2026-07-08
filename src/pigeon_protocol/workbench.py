"""全自动纯协议工作台：会话列表 → 当前买家 → 懒加载上下文/订单 → WS 监听。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Callable

from pigeon_protocol.conv_list import list_conversations_relay, parse_conversation_items
from pigeon_protocol.models import ConversationContext, InboundMessage, OrderContext
from pigeon_protocol.session import load_session

logger = logging.getLogger("pigeon.workbench")


def _uid_from_msg(msg: InboundMessage) -> str:
    uid = (msg.security_user_id or "").strip()
    if uid.startswith("AQ"):
        return uid
    route = msg.conversation_id or msg.conversation_route or ""
    if route.startswith("AQ"):
        return route.split(":")[0]
    return ""


def _ctx_summary(ctx: ConversationContext) -> dict[str, Any]:
    return {
        "security_user_id": ctx.security_user_id,
        "buyer_name": ctx.buyer_name,
        "message_count": len(ctx.messages),
        "source": ctx.source,
        "preview": [
            {"role": m.get("role"), "text": str(m.get("text") or "")[:120]}
            for m in ctx.messages[-8:]
        ],
    }


def _ord_summary(ord_ctx: OrderContext) -> dict[str, Any]:
    return {
        "has_order": ord_ctx.has_order,
        "source": ord_ctx.source,
        "summary": ord_ctx.summary,
        "orders": ord_ctx.orders[:5],
    }


class PureWorkbench:
    """无需手动 buyer-id：自动拉列表、选当前会话、监听并懒加载。"""

    def __init__(
        self,
        *,
        emit: Callable[[dict[str, Any]], None] | None = None,
        conv_refresh_sec: int = 60,
    ) -> None:
        os.environ.setdefault("PIGEON_STANDALONE", "1")
        from pigeon_protocol.config import AppConfig
        from pigeon_protocol.standalone import StandaloneRuntime

        self.runtime = StandaloneRuntime(config=AppConfig(dry_run=False))
        self.emit = emit or _default_emit
        self.conv_refresh_sec = conv_refresh_sec
        self.current_uid: str = ""
        self.conversations: list[dict[str, Any]] = []
        self.by_queue: dict[str, list[dict[str, Any]]] = {}
        self._context_cache: dict[str, ConversationContext] = {}
        self._orders_cache: dict[str, OrderContext] = {}
        self._loading: set[str] = set()
        self._stop = threading.Event()

    def _event(self, kind: str, **payload: Any) -> None:
        self.emit({"kind": kind, "ts": int(time.time()), **payload})

    def heal_session(self) -> dict[str, Any]:
        from pigeon_protocol.session_health import auto_heal_session

        report = auto_heal_session(self.runtime.session).to_dict()
        self.runtime.session = load_session()
        self.runtime._reload()
        self._event("session_heal", report=report)
        return report

    def load_conversations(self, *, size: int = 30) -> dict[str, Any]:
        from pigeon_protocol.config import XUNDAN_QUEUE_KEYS

        session = self.runtime.session
        raw = list_conversations_relay(session, size=size, queue_keys=XUNDAN_QUEUE_KEYS)
        items = parse_conversation_items(raw)
        by_queue: dict[str, list[dict[str, Any]]] = {}

        if raw.get("via", "").startswith("conv_list/fallback") or raw.get("whale_blocked"):
            by_queue["fallback"] = items
        else:
            for it in items:
                qk = str(it.get("queue_key") or "all")
                by_queue.setdefault(qk, []).append(it)

        if not items:
            from pigeon_protocol.session_health import auto_heal_session

            auto_heal_session(session, refresh_csrf=True, refresh_sign=True)
            raw = list_conversations_relay(session, size=size, queue_keys=XUNDAN_QUEUE_KEYS)
            items = parse_conversation_items(raw)
            if raw.get("via", "").startswith("conv_list/fallback") or raw.get("whale_blocked"):
                by_queue = {"fallback": items}
            else:
                by_queue = {}
                for it in items:
                    qk = str(it.get("queue_key") or "all")
                    by_queue.setdefault(qk, []).append(it)

        if not items:
            fallback = self.runtime.context.list_conversations(page=0, size=size)
            items = parse_conversation_items(fallback)
            by_queue["fuzzySearch"] = items

        self.conversations = items
        self.by_queue = by_queue
        self._event(
            "conversations",
            total=len(items),
            by_queue={k: len(v) for k, v in by_queue.items()},
            items=items,
        )
        return {"total": len(items), "by_queue": by_queue, "items": items}

    def pick_current(self, uid: str = "") -> str:
        if uid:
            self.current_uid = uid
        elif self.conversations:
            for prefer in ("all", "no_order", "pending", "no_pay", "after_sale"):
                for it in self.by_queue.get(prefer, []):
                    cand = str(it.get("security_user_id") or "")
                    if cand:
                        self.current_uid = cand
                        break
                if self.current_uid:
                    break
            if not self.current_uid:
                self.current_uid = str(self.conversations[0].get("security_user_id") or "")
        self._event(
            "current",
            security_user_id=self.current_uid,
            name=_conv_name(self.conversations, self.current_uid),
        )
        return self.current_uid

    def ensure_loaded(self, uid: str, *, reason: str = "lazy", sync: bool = False) -> None:
        if not uid:
            return
        if not sync:

            def _work() -> None:
                self._load_one(uid, reason=reason)

            if uid in self._loading:
                return
            threading.Thread(target=_work, daemon=True, name=f"load-{uid[:12]}").start()
            return
        self._load_one(uid, reason=reason)

    def _load_one(self, uid: str, *, reason: str) -> None:
        if uid in self._loading:
            return
        self._loading.add(uid)
        try:
            ctx = self.runtime.get_context(uid, merge_ws=True)
            orders = self.runtime.get_orders(uid)
            self._context_cache[uid] = ctx
            self._orders_cache[uid] = orders
            self._event("context", reason=reason, **_ctx_summary(ctx))
            self._event("orders", reason=reason, security_user_id=uid, **_ord_summary(orders))
        except Exception as exc:
            self._event("load_error", security_user_id=uid, error=str(exc))
        finally:
            self._loading.discard(uid)

    def set_current(self, uid: str, *, reason: str = "select") -> None:
        if not uid:
            return
        self.current_uid = uid
        self._event(
            "current",
            security_user_id=uid,
            name=_conv_name(self.conversations, uid),
            reason=reason,
        )
        self.ensure_loaded(uid, reason=reason)

    def _on_ws_message(self, msg: InboundMessage) -> None:
        uid = _uid_from_msg(msg)
        self._event(
            "message",
            message={
                "role": msg.role,
                "text": msg.text,
                "security_user_id": uid,
                "nickname": msg.nickname,
                "conversation_id": msg.conversation_id,
            },
        )
        if uid and uid != self.current_uid:
            self.set_current(uid, reason="ws_inbound")
        elif uid and uid == self.current_uid:
            if uid in self._context_cache:
                merged = self.runtime.store.merge_context(self._context_cache[uid])
                self._context_cache[uid] = merged
                self._event("context", reason="ws_merge", **_ctx_summary(merged))

    async def _listen_loop(self, *, chunk_sec: int = 120) -> None:
        while not self._stop.is_set():
            try:
                await self.runtime.listen(self._on_ws_message, timeout_sec=chunk_sec)
            except Exception as exc:
                self._event("listen_error", error=str(exc))
                await asyncio.sleep(2)

    async def _conv_refresh_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.conv_refresh_sec)
            try:
                before = self.current_uid
                self.load_conversations()
                if before:
                    self.current_uid = before
            except Exception as exc:
                self._event("conv_refresh_error", error=str(exc))

    def bootstrap(self) -> dict[str, Any]:
        heal = self.heal_session()
        conv = self.load_conversations()
        uid = self.pick_current()
        if uid:
            self.ensure_loaded(uid, reason="bootstrap", sync=True)
        return {"heal": heal, "conversations": len(conv.get("items") or []), "current_uid": uid}

    async def run_async(self, *, listen: bool = True) -> None:
        boot = self.bootstrap()
        self._event("ready", **boot)
        tasks = []
        if listen:
            tasks.append(asyncio.create_task(self._listen_loop()))
        if self.conv_refresh_sec > 0:
            tasks.append(asyncio.create_task(self._conv_refresh_loop()))
        try:
            await asyncio.gather(*tasks)
        finally:
            self._stop.set()

    def run(self, *, listen: bool = True) -> None:
        try:
            asyncio.run(self.run_async(listen=listen))
        except KeyboardInterrupt:
            self._stop.set()
            self._event("stopped", reason="keyboard")


def _conv_name(conversations: list[dict[str, Any]], uid: str) -> str:
    for it in conversations:
        if it.get("security_user_id") == uid:
            return str(it.get("name") or "")
    return ""


def _default_emit(event: dict[str, Any]) -> None:
    print(json.dumps(event, ensure_ascii=False, default=str), flush=True)


def run_workbench(
    *,
    listen: bool = True,
    conv_refresh_sec: int = 60,
    bootstrap_only: bool = False,
) -> dict[str, Any]:
    """One-shot bootstrap (no listen) or full run."""
    wb = PureWorkbench(conv_refresh_sec=conv_refresh_sec)
    if bootstrap_only:
        return wb.bootstrap()
    wb.run(listen=listen)
    return {"ok": True}
