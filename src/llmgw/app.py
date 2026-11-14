"""Gateway ASGI app: routes, client auth, request size limit, lifecycle.

The gateway state (config, event writer, ruleset, upstream transport) is
built in the lifespan. ``Gateway.ruleset`` is swapped by reference on hot
reload (D8); each request captures the reference once at start (D12).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import proxy, routing
from .auth import authenticate, extract_token
from .config import GatewayConfig, Upstream, load_config, resolve_canary_tokens
from .events import (
    EventWriter,
    RequestContext,
    auth_failed_event,
    new_ulid,
    request_completed_event,
    ruleset_loaded_event,
)
from .formats import anthropic as anthropic_fmt
from .formats import openai as openai_fmt
from .formats._wire import DuplicateKeyError, FormatError
from .rules.loader import RulesetLoadError, load_rules
from .rules.ruleset import Ruleset
from .rules.watcher import RulesWatcher

_AUTH_MESSAGES = {
    "openai": "Invalid or missing client token.",
    "anthropic": "Invalid or missing x-api-key or authorization token.",
}


@dataclass
class Gateway:
    """Per-process gateway state; ``ruleset`` is swapped by reference (D8)."""

    config: GatewayConfig
    writer: EventWriter
    ruleset: Ruleset
    transport: httpx.AsyncBaseTransport | None = None
    # One pooled client per upstream (design D2); closed in the lifespan.
    clients: dict[str, httpx.AsyncClient] = field(default_factory=dict)

    def client_for(self, upstream: Upstream) -> httpx.AsyncClient:
        return self.clients[upstream.name]


def create_app(
    config: GatewayConfig | str | Path,
    *,
    canary_values: list[str] | None = None,
    upstream_transport: httpx.AsyncBaseTransport | None = None,
    event_writer: EventWriter | None = None,
) -> Starlette:
    """Build the gateway app.

    ``config`` may be a ``GatewayConfig`` or a path to ``gateway.yaml``.
    ``canary_values`` overrides the env-var-sourced canaries (replay tool).
    ``upstream_transport`` injects a fake transport for tests.
    """
    if not isinstance(config, GatewayConfig):
        config = load_config(config)
    own_writer = event_writer is None
    writer = event_writer or EventWriter(config.events.path, stdout=config.events.stdout)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        canaries = (
            canary_values
            if canary_values is not None
            else resolve_canary_tokens(config)
        )
        try:
            result = load_rules(config.rules_dir, event_sink=writer.emit, canary_values=canaries)
        except RulesetLoadError as exc:
            raise RuntimeError(f"cannot read rules directory {config.rules_dir}: {exc}") from exc
        writer.emit(
            ruleset_loaded_event(
                ruleset_hash=result.ruleset.hash,
                accepted=result.accepted,
                rejected=result.rejected,
            )
        )
        gateway = Gateway(
            config=config,
            writer=writer,
            ruleset=result.ruleset,
            transport=upstream_transport,
        )
        app.state.gateway = gateway
        for name in config.upstreams:
            gateway.clients[name] = httpx.AsyncClient(
                transport=upstream_transport, timeout=config.upstream_timeout_s
            )
        watcher = RulesWatcher(gateway, canary_values=canaries)
        watcher.start()
        app.state.watcher = watcher
        try:
            yield
        finally:
            watcher.stop()
            for client in gateway.clients.values():
                await client.aclose()
            if own_writer:
                writer.close()

    return Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/v1/models", list_models, methods=["GET"]),
            Route("/v1/chat/completions", chat_completions, methods=["POST"]),
            Route("/v1/messages", messages, methods=["POST"]),
            Route("/{path:path}", not_found, methods=["GET", "POST"]),
        ],
        lifespan=lifespan,
    )


def _ms(started: float) -> float:
    return (time.monotonic() - started) * 1000


def _error_response(
    fmt: str,
    status: int,
    message: str,
    *,
    request_id: str,
    event_id: str | None = None,
    type_override: str | None = None,
    param: str | None = None,
    code: str | None = None,
) -> Response:
    if fmt == "openai":
        body = openai_fmt.error_body(
            status, message, type_override=type_override, param=param, code=code
        )
    else:
        body = anthropic_fmt.error_body(status, message, type_override=type_override)
    response = Response(content=body, status_code=status, media_type="application/json")
    response.headers["x-gateway-request-id"] = request_id
    if event_id:
        response.headers["x-gateway-event-id"] = event_id
    return response


def _authenticate(gw: Gateway, request: Request, ctx: RequestContext) -> bool:
    """Run the auth check and emit its events; True when the client matched."""
    token, style = extract_token(request.headers)
    client = authenticate(gw.config, token) if token is not None else None
    source = request.client.host if request.client else None
    if client is None:
        gw.writer.emit(auth_failed_event(ctx, source=source, style=style))
        return False
    ctx.client = client.name
    return True


async def _read_body_capped(request: Request, limit: int) -> bytes | None:
    """Read the request body up to ``limit`` bytes; None when it exceeds them.

    The read stops at the cap so an oversized body is never held in memory in
    full (design D4b).
    """
    chunks: list[bytes] = []
    total = 0
    stream = request.stream()
    while True:
        try:
            chunk = await stream.__anext__()
        except StopAsyncIteration:
            break
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def healthz(request: Request) -> Response:
    """Unauthenticated health probe; reveals nothing (client-auth: Health endpoint)."""
    return JSONResponse({"status": "ok"})


async def not_found(request: Request) -> Response:
    """Unsupported paths 404 without auth, events, or upstream contact."""
    return Response(
        openai_fmt.error_body(404, "not found"), status_code=404, media_type="application/json"
    )


async def list_models(request: Request) -> Response:
    gw: Gateway = request.app.state.gateway
    config = gw.config
    started = time.monotonic()
    ctx = RequestContext(
        request_id=new_ulid(), mode=config.mode, ruleset_hash=gw.ruleset.hash
    )
    if not _authenticate(gw, request, ctx):
        gw.writer.emit(
            request_completed_event(ctx, outcome="auth_failed", status=401, latency_ms=_ms(started))
        )
        return _error_response("openai", 401, _AUTH_MESSAGES["openai"], request_id=ctx.request_id)
    data = [
        {"id": name, "object": "model", "created": 0, "owned_by": "sec-gateway"}
        for name in config.models
    ]
    response = JSONResponse({"object": "list", "data": data})
    response.headers["x-gateway-request-id"] = ctx.request_id
    gw.writer.emit(
        request_completed_event(ctx, outcome="allowed", status=200, latency_ms=_ms(started))
    )
    return response


async def chat_completions(request: Request) -> Response:
    return await _chat(request, fmt="openai", path="/v1/chat/completions")


async def messages(request: Request) -> Response:
    return await _chat(request, fmt="anthropic", path="/v1/messages")


async def _chat(request: Request, *, fmt: str, path: str) -> Response:
    gw: Gateway = request.app.state.gateway
    config = gw.config
    ruleset = gw.ruleset  # captured once at start; a reload mid-request is ignored
    started = time.monotonic()
    ctx = RequestContext(
        request_id=new_ulid(), mode=config.mode, ruleset_hash=ruleset.hash
    )

    # Auth before any body read, parsing, or upstream contact.
    if not _authenticate(gw, request, ctx):
        gw.writer.emit(
            request_completed_event(ctx, outcome="auth_failed", status=401, latency_ms=_ms(started))
        )
        return _error_response(fmt, 401, _AUTH_MESSAGES[fmt], request_id=ctx.request_id)

    body = await _read_body_capped(request, config.max_request_bytes)
    if body is None:
        gw.writer.emit(
            request_completed_event(
                ctx, outcome="upstream_error", status=413, latency_ms=_ms(started)
            )
        )
        return _error_response(
            fmt,
            413,
            "Request body exceeds the maximum allowed size.",
            request_id=ctx.request_id,
        )

    try:
        model, data = routing.parse_chat_body(body)
    except DuplicateKeyError:
        gw.writer.emit(
            request_completed_event(
                ctx, outcome="upstream_error", status=400, latency_ms=_ms(started)
            )
        )
        return _error_response(
            fmt,
            400,
            "Invalid JSON: duplicate keys are not allowed.",
            request_id=ctx.request_id,
        )
    except routing.MissingModelError:
        gw.writer.emit(
            request_completed_event(
                ctx, outcome="upstream_error", status=400, latency_ms=_ms(started)
            )
        )
        return _error_response(
            fmt,
            400,
            "Missing required parameter: 'model'.",
            request_id=ctx.request_id,
            param="model",
        )
    except FormatError:
        gw.writer.emit(
            request_completed_event(
                ctx, outcome="upstream_error", status=400, latency_ms=_ms(started)
            )
        )
        return _error_response(
            fmt, 400, "Invalid JSON in request body.", request_id=ctx.request_id
        )

    try:
        upstream = routing.resolve_upstream(config, model)
    except routing.UnknownModelError:
        gw.writer.emit(
            request_completed_event(
                ctx, outcome="upstream_error", status=404, latency_ms=_ms(started)
            )
        )
        return _error_response(
            fmt,
            404,
            f"Model not found: {model}.",
            request_id=ctx.request_id,
            code="model_not_found",
            param="model",
        )

    return await proxy.forward_chat(
        gw,
        ruleset,
        ctx,
        fmt=fmt,
        path=path,
        model=model,
        upstream=upstream,
        body=body,
        client_headers=dict(request.headers),
        started=started,
        stream=data.get("stream") is True,
    )
