"""
The caller's IP address (enterprise security programme S3).

With TRUSTED_PROXY_HOPS = 0 (default) it is the TCP peer. Behind N trusted
proxies that each append to X-Forwarded-For, it is the Nth address from the
right: the one the outermost trusted proxy saw. Addresses further left were
written by the client and are never trusted, so a client cannot spoof its way
past an IP allowlist by sending its own X-Forwarded-For.
"""
from __future__ import annotations

import ipaddress

from fastapi import Request

from app.core.config import get_settings


def client_ip(request: Request) -> str | None:
    peer = request.client.host if request.client else None
    hops = get_settings().trusted_proxy_hops
    if hops <= 0:
        return peer
    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if len(chain) < hops:
        return peer
    candidate = chain[-hops]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer
