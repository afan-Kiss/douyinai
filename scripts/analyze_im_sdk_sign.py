#!/usr/bin/env python3
"""Extract sign-related snippets from downloaded IM SDK chunks."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "analysis" / "im_sdk"
OUT = ROOT / "analysis" / "im_sdk_sign_candidates.json"

NEEDLES = [
    "s:client_message_id",
    "client_message_id",
    "pigeon_sign",
    "WebSocket",
    "get_message_by_init",
    "cloudSendMessage",
    "wasm",
    "instantiate",
    "base64",
    "encrypt",
    "sign",
]


def snippets(text: str, needle: str, *, window: int = 120, limit: int = 5) -> list[dict]:
    out: list[dict] = []
    for m in re.finditer(re.escape(needle), text, re.I):
        pos = m.start()
        if out and pos - out[-1]["pos"] < 40:
            continue
        out.append(
            {
                "needle": needle,
                "pos": pos,
                "ctx": text[max(0, pos - window) : pos + window].replace("\n", " "),
            }
        )
        if len(out) >= limit:
            break
    return out


def main() -> int:
    manifest_path = ROOT / "analysis" / "im_sdk_manifest.json"
    files: list[str] = []
    if manifest_path.is_file():
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = [c["file"] for c in doc.get("chunks", [])[:12]]
    else:
        files = [p.name for p in sorted(SDK.glob("*.js"))]

    report: dict = {"files": [], "top_snippets": []}
    for name in files:
        path = SDK / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        file_hits: dict[str, int] = {}
        file_snips: list[dict] = []
        for needle in NEEDLES:
            count = len(re.findall(re.escape(needle), text, re.I))
            if count:
                file_hits[needle] = count
                if needle in ("s:client_message_id", "pigeon_sign", "WebSocket", "wasm", "get_message_by_init"):
                    file_snips.extend(snippets(text, needle, limit=3))
        if file_hits:
            report["files"].append({"file": name, "hits": file_hits, "snippets": file_snips[:12]})
            report["top_snippets"].extend(file_snips[:4])

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"files": len(report["files"]), "out": str(OUT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
