from __future__ import annotations

from pigeon_protocol.http_client import BackstageHttpClient
from pigeon_protocol.models import OrderContext
from pigeon_protocol.session import SessionState


class OrderService:
    def __init__(self, session: SessionState, *, dry_run: bool = False, use_cdp_sign: bool = False) -> None:
        self.session = session
        self.http = BackstageHttpClient(session, dry_run=dry_run, use_cdp_sign=use_cdp_sign)

    def get_orders(self, security_user_id: str, *, via_cdp: bool = False) -> OrderContext:
        if via_cdp:
            self.http.use_cdp_sign = True
        return self.http.query_orders(security_user_id)

    @staticmethod
    def cdp_available() -> bool:
        from pigeon_protocol.sign import CdpSigner

        return CdpSigner.available()
