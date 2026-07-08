"""Whale-protected backstage `_v` — mirrors IM SDK E.AM() from gfdatav1.ver."""
from __future__ import annotations

import re


def extract_gfdata_ver(html: str) -> str:
    if not html:
        return ""
    m = re.search(r'gfdatav1\s*=\s*\{[^}]*"ver"\s*:\s*"([^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'"ver"\s*:\s*"(\d+\.\d+\.\d+\.\d+)"', html)
    return m.group(1) if m else ""


def extract_im_pc_version(html: str) -> str:
    """deskVersion for X-IM-PC-Version — vmok pigeon-im-pc module version."""
    if not html:
        return ""
    hits = re.findall(r"@ecom-vmok/pigeon-im-pc:(\d+\.\d+\.\d+\.\d+)", html)
    if hits:
        return max(hits, key=lambda x: int(x.rsplit(".", 1)[-1]))
    gf = extract_gfdata_ver(html)
    return gf


def compute_whale_v(gfdata_ver: str, *, is_desk: bool = False) -> str:
    """gfdatav1.ver last segment + 1401 (web) or +695 (desktop)."""
    parts = (gfdata_ver or "1.0.0.0").split(".")
    last = parts.pop() if parts else "0"
    try:
        bump = 695 if is_desk else 1401
        parts.append(str(int(last or "0") + bump))
    except ValueError:
        parts.append(last)
    return ".".join(parts)


def resolve_whale_versions(*, html: str = "", session=None) -> dict[str, str]:
    if not html and session is not None:
        try:
            from pigeon_protocol.feige_init import _fetch_workspace_html

            html = _fetch_workspace_html(session)
        except Exception:
            html = ""

    gf = extract_gfdata_ver(html)
    im_pc = extract_im_pc_version(html)
    whale_v = compute_whale_v(gf) if gf else ""

    if not whale_v:
        from pigeon_protocol.account_context import analysis_env_file, bundle_file

        for p in (bundle_file("bdms_browser_env.json"), analysis_env_file()):
            try:
                import json

                if not p.is_file():
                    continue
                tpl = json.loads(p.read_text(encoding="utf-8")).get("convListTemplate") or {}
                whale_v = str(tpl.get("_v") or "")
                if whale_v:
                    break
            except OSError:
                continue

    if not im_pc and gf:
        im_pc = gf

    return {
        "gfdata_ver": gf,
        "whale_v": whale_v,
        "im_pc_version": im_pc,
    }
