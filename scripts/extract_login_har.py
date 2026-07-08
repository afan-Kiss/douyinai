#!/usr/bin/env python3
"""Extract QR login SSO template from Chrome HAR (e.g. 登录.har)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def extract(har_path: Path) -> dict:
    from pigeon_protocol.qr_login import _extract_har_template

    data = _extract_har_template(har_path)
    data.setdefault("sso_host", "https://doudian-sso.jinritemai.com")
    data.setdefault("aid", 4272)
    data.setdefault("subject_aid", 4966)
    data.setdefault("service", "https://fxg.jinritemai.com/login/common")
    data.setdefault("referer", "https://fxg.jinritemai.com/login/common?channel=zhaoshang")
    data.setdefault("origin", "https://fxg.jinritemai.com")
    data["source_har"] = str(har_path)
    return data


def main() -> int:
    har_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\1\Desktop\登录.har")
    if not har_path.is_file():
        print(f"HAR not found: {har_path}", file=sys.stderr)
        return 1
    out = ROOT / "analysis" / "login_qr_template.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    data = extract(har_path)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
