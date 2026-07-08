#!/usr/bin/env python3
"""CDP warm session inners — CLI wrapper."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_protocol.cdp_warm_inners import warm_session_inners_async  # noqa: E402


async def main() -> int:
    report = await warm_session_inners_async(launch=False)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
