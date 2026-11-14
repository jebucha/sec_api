"""Client token authentication.

Tokens are configured as SHA-256 hex digests; presented tokens are hashed and
compared with ``hmac.compare_digest`` (constant time). Both SDK header styles
are accepted: ``Authorization: Bearer <token>`` and ``x-api-key: <token>``.
"""

from __future__ import annotations

import hashlib
import hmac

from .config import Client, GatewayConfig


def sha256_hex(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def extract_token(headers) -> tuple[str | None, str | None]:
    """Extract the client token from request headers.

    Returns ``(token, style)`` where style is ``"bearer"`` or ``"x-api-key"``.
    ``Authorization`` takes precedence when it carries a Bearer token.
    """
    authorization = headers.get("authorization")
    if authorization is not None:
        prefix = "bearer "
        if authorization[: len(prefix)].lower() == prefix:
            token = authorization[len(prefix) :].strip()
            if token:
                return token, "bearer"
    x_api_key = headers.get("x-api-key")
    if x_api_key is not None and x_api_key.strip():
        return x_api_key.strip(), "x-api-key"
    return None, None


def authenticate(config: GatewayConfig, token: str) -> Client | None:
    """Match a presented token against configured client hashes, constant time."""
    digest = sha256_hex(token)
    for client in config.clients.values():
        if hmac.compare_digest(digest, client.token_sha256):
            return client
    return None
