"""Hashing helpers for the privacy constraints in section 7.

Neither raw client IPs nor source URLs are ever persisted. These functions are
the only sanctioned way to derive the stored forms.
"""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlsplit, urlunsplit

from fastapi import Request

# Full SHA-256 hex is 64 chars; 32 is ample for dedup and halves the index size.
URL_HASH_LENGTH = 32


def hash_ip(ip: str | None, salt: str) -> str | None:
    """Salted SHA-256 of a client IP. The raw value never leaves the request."""
    if not ip:
        return None
    return hmac.new(salt.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256).hexdigest()


def normalise_url(url: str) -> str:
    """Canonical form for hashing, so trivially different URLs dedup together."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def hash_url(url: str, salt: str) -> str:
    """Truncated salted digest -- used for dedup only, never reversed."""
    digest = hmac.new(
        salt.encode("utf-8"), normalise_url(url).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return digest[:URL_HASH_LENGTH]


def client_ip(request: Request) -> str | None:
    """Best-effort client IP.

    Cloud Run and Vercel both terminate TLS upstream, so X-Forwarded-For is the
    real source. We take the left-most entry, which is the original client.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else None
