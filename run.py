#!/usr/bin/env python3
"""Entry point: python run.py status"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pigeon_protocol.cli import main

if __name__ == "__main__":
    try:
        from pigeon_protocol.runtime_paths import apply_runtime_env

        apply_runtime_env()
    except Exception:
        pass
    raise SystemExit(main())
