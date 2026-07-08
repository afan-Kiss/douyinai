"""HTTP transports — httpx default, curl_cffi Chrome TLS impersonation for bdms-bound APIs."""
from __future__ import annotations

import json
from typing import Any

import httpx


def curl_cffi_available() -> bool:
    try:
        import curl_cffi.requests  # noqa: F401

        return True
    except ImportError:
        return False


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
    timeout: float = 15.0,
    transport: str = "httpx",
    impersonate: str = "chrome131",
) -> dict[str, Any]:
    if transport == "curl_cffi":
        return _curl_cffi_request(
            method,
            url,
            headers=headers,
            json_body=json_body,
            timeout=timeout,
            impersonate=impersonate,
        )
    return _httpx_request(method, url, headers=headers, json_body=json_body, timeout=timeout)


def _httpx_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.request(method, url, headers=headers, json=json_body)
        parsed: Any
        try:
            parsed = resp.json()
        except Exception:
            parsed = {"raw_text": resp.text[:2000]}
        return {
            "ok": resp.status_code == 200,
            "status": resp.status_code,
            "url": str(resp.url),
            "data": parsed,
            "headers": dict(resp.headers),
            "transport": "httpx",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "url": url, "body": json_body, "transport": "httpx"}


def _curl_cffi_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None,
    timeout: float,
    impersonate: str,
) -> dict[str, Any]:
    if not curl_cffi_available():
        return {"ok": False, "error": "curl_cffi not installed", "url": url, "transport": "curl_cffi"}

    from curl_cffi import requests as curl_requests

    try:
        resp = curl_requests.request(
            method,
            url,
            headers=headers,
            json=json_body,
            impersonate=impersonate,
            timeout=timeout,
            allow_redirects=True,
        )
        parsed: Any
        try:
            parsed = resp.json()
        except Exception:
            parsed = {"raw_text": (resp.text or "")[:2000]}
        return {
            "ok": resp.status_code == 200,
            "status": resp.status_code,
            "url": str(resp.url),
            "data": parsed,
            "headers": dict(resp.headers),
            "transport": "curl_cffi",
            "impersonate": impersonate,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "url": url, "body": json_body, "transport": "curl_cffi"}


def order_api_ok(result: dict[str, Any]) -> bool:
    if result.get("dry_run"):
        return False
    if not result.get("ok"):
        return False
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if "raw_text" in data and "code" not in data:
        return False
    code = data.get("code")
    if code is None:
        return False
    code = str(code)
    if code in ("0", "0.0"):
        return True
    if data.get("componentized_data") or data.get("data"):
        return True
    return False
