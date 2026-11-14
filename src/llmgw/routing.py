"""Model-to-upstream routing and the header forwarding policy (design D4a).

Model lookup, request/response header shaping, and upstream credential
injection live here so the proxy layer only deals with bytes and verdicts.
"""

from __future__ import annotations

from collections.abc import Mapping

from .config import GatewayConfig, Upstream
from .formats._wire import DuplicateKeyError, FormatError, parse_json_strict

# Hop-by-hop headers (RFC 7230 section 6.1) plus Connection-named headers.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


class RoutingError(Exception):
    """A request cannot be routed to an upstream."""


class UnknownModelError(RoutingError):
    def __init__(self, model: str) -> None:
        super().__init__(f"model not found: {model}")
        self.model = model


class MissingModelError(RoutingError):
    def __init__(self) -> None:
        super().__init__("request body is missing the 'model' field")


def parse_chat_body(body: bytes) -> tuple[str, dict]:
    """Parse a chat request body: ``(model, data)``.

    Raises ``FormatError`` for invalid JSON, ``DuplicateKeyError`` for
    duplicate JSON keys, and ``MissingModelError`` when ``model`` is absent
    or not a non-empty string.
    """
    try:
        data = parse_json_strict(body)
    except DuplicateKeyError:
        raise
    except Exception as exc:  # json.JSONDecodeError and friends
        raise FormatError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise FormatError("request body must be a JSON object")
    model = data.get("model")
    if not isinstance(model, str) or not model:
        raise MissingModelError()
    return model, data


def resolve_upstream(config: GatewayConfig, model: str) -> Upstream:
    """The configured upstream serving ``model``; UnknownModelError if unmapped."""
    upstream_name = config.models.get(model)
    if upstream_name is None:
        raise UnknownModelError(model)
    return config.upstreams[upstream_name]


def upstream_url(upstream: Upstream, path: str) -> str:
    """The upstream's base URL plus the client's original API path."""
    return f"{upstream.url}{path}"


def _connection_named(headers: dict[str, str]) -> set[str]:
    return {
        name.strip().lower() for name in headers.get("connection", "").split(",") if name.strip()
    }


def forward_headers(client_headers: Mapping[str, str]) -> dict[str, str]:
    """Client request headers shaped for the upstream (design D4a).

    Strips the client's credentials (``Authorization``/``x-api-key``), all
    hop-by-hop headers (including any named in ``Connection``), ``Host`` and
    ``Content-Length`` (httpx recomputes both from the upstream URL and the
    unchanged bytes), and forces ``Accept-Encoding: identity`` so the bytes
    the gateway inspects are exactly the bytes it relays.
    """
    headers = {key.lower(): value for key, value in client_headers.items()}
    extra = _connection_named(headers)
    for name in list(headers):
        if name in HOP_BY_HOP or name in extra:
            del headers[name]
    headers.pop("authorization", None)
    headers.pop("x-api-key", None)
    headers.pop("host", None)
    headers.pop("content-length", None)
    headers["accept-encoding"] = "identity"
    return headers


def inject_upstream_credential(headers: dict[str, str], upstream: Upstream) -> dict[str, str]:
    """Add the upstream's credential in its configured style, or nothing."""
    if upstream.auth.style == "bearer" and upstream.auth.secret is not None:
        headers["authorization"] = f"Bearer {upstream.auth.secret}"
    elif upstream.auth.style == "x-api-key" and upstream.auth.secret is not None:
        headers["x-api-key"] = upstream.auth.secret
    return headers


def relay_response_headers(upstream_headers: Mapping[str, str]) -> dict[str, str]:
    """Upstream response headers shaped for the client (design D4a).

    Drops hop-by-hop headers and ``Content-Length`` (Starlette recomputes it
    from the relayed bytes); everything else passes through.
    """
    headers = {key.lower(): value for key, value in upstream_headers.items()}
    extra = _connection_named(headers)
    for name in list(headers):
        if name in HOP_BY_HOP or name in extra:
            del headers[name]
    headers.pop("content-length", None)
    return headers
