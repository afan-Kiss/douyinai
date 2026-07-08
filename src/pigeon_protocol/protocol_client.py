"""Unified pure-protocol client — alias for PureProtocolRuntime."""
from __future__ import annotations

from pigeon_protocol.pure_runtime import PureProtocolRuntime

ProtocolClient = PureProtocolRuntime

__all__ = ["ProtocolClient", "PureProtocolRuntime"]
