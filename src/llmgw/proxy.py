"""Chat request pipeline: request-stage inspection, upstream forwarding,
response-stage inspection, and response relay (sections 7 and 8).

The proxy never mutates the request body: canonical normalization is
inspection-only, and the original bytes are forwarded unchanged (D5).

Streaming (design D9):
- monitor (policy ``flag``): a tee generator relays each upstream chunk
  immediately and accumulates up to ``max_buffer_bytes``; response-stage
  evaluation and events run after the stream ends.
- enforce, or monitor with ``uninspected_policy: fail_closed``: the full
  stream is buffered (chunk boundaries preserved), evaluated, then replayed
  as the exact original chunks or replaced by a 403 block. Overflow is a
  detection error: block + ``rule_error``. (A stream cannot be blocked after
  the fact, so fail_closed buffers even in monitor mode — design D12.)
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import httpx
from starlette.responses import Response, StreamingResponse

from . import routing
from .config import GatewayConfig, Upstream
from .engine import evaluate
from .events import (
    EventWriter,
    RequestContext,
    request_completed_event,
    rule_error_event,
    uninspected_content_event,
    verdict_event,
)
from .formats import anthropic as anthropic_fmt
from .formats import openai as openai_fmt
from .formats.canonical import GAP_OTHER, GAP_STREAM_ERROR, GAP_STREAM_OVERFLOW, Canonical, Gap
from .rules.ruleset import Ruleset

if TYPE_CHECKING:
    from .app import Gateway

_FORMATS = {"openai": openai_fmt, "anthropic": anthropic_fmt}


def _fmt(fmt: str):
    return _FORMATS[fmt]


def _ms(started: float) -> float:
    return (time.monotonic() - started) * 1000


def _upstream_error(
    gw: Gateway, ctx: RequestContext, fmt: str, status: int, message: str, started: float
) -> Response:
    gw.writer.emit(
        request_completed_event(
            ctx, outcome="upstream_error", status=status, latency_ms=_ms(started)
        )
    )
    body = _fmt(fmt).error_body(status, message)
    response = Response(content=body, status_code=status, media_type="application/json")
    response.headers["x-gateway-request-id"] = ctx.request_id
    return response


def _block_message(config: GatewayConfig, rule_ids: list[str], event_id: str) -> str:
    """Generic policy message; rule ids only with ``debug_verdicts``."""
    message = f"request blocked by security policy (event: {event_id})"
    if config.debug_verdicts and rule_ids:
        message += f" (rules: {', '.join(rule_ids)})"
    return message


def _block_response(
    gw: Gateway,
    ctx: RequestContext,
    fmt: str,
    *,
    event_id: str,
    rule_ids: list[str],
    started: float,
) -> Response:
    message = _block_message(gw.config, rule_ids, event_id)
    body = _fmt(fmt).error_body(403, message, type_override="gateway_blocked")
    response = Response(content=body, status_code=403, media_type="application/json")
    response.headers["x-gateway-event-id"] = event_id
    response.headers["x-gateway-request-id"] = ctx.request_id
    gw.writer.emit(
        request_completed_event(ctx, outcome="blocked", status=403, latency_ms=_ms(started))
    )
    return response


def _emit_gaps(writer: EventWriter, ctx: RequestContext, canonical: Canonical, stage: str) -> None:
    for gap in canonical.uninspected:
        writer.emit(
            uninspected_content_event(
                ctx, kind=gap.kind, location=gap.location, detail=gap.detail, stage=stage
            )
        )


def _response_stage(
    gw: Gateway,
    ruleset: Ruleset,
    ctx: RequestContext,
    fmt: str,
    model: str,
    upstream: Upstream,
    canonical: Canonical,
) -> tuple[str, list[str]] | None:
    """Response-stage inspection: emit gap/verdict/rule_error events.

    Returns ``(event_id, rule_ids)`` when the stage must be blocked, else
    None.
    """
    writer = gw.writer
    config = gw.config
    _emit_gaps(writer, ctx, canonical, "response")
    fail_closed = bool(canonical.uninspected) and config.uninspected_policy == "fail_closed"
    verdict = evaluate(
        ruleset,
        canonical,
        stage="response",
        mode=config.mode,
        excerpt_max_chars=config.excerpt_max_chars,
    )
    for error in verdict.rule_errors:
        writer.emit(rule_error_event(ctx, rule_id=error.rule_id, error=error.error))
    if fail_closed or verdict.blocked:
        event_id = writer.emit(
            verdict_event(
                ctx,
                stage="response",
                format=fmt,
                model=model,
                upstream=upstream.name,
                action="block",
                enforced=True,
                matches=list(verdict.matches),
            )
        )
        return event_id, [match.rule_id for match in verdict.matches]
    if verdict.action != "allow":
        writer.emit(
            verdict_event(
                ctx,
                stage="response",
                format=fmt,
                model=model,
                upstream=upstream.name,
                action=verdict.action,
                enforced=False,
                matches=list(verdict.matches),
            )
        )
    return None


def _request_stage(
    gw: Gateway,
    ruleset: Ruleset,
    ctx: RequestContext,
    fmt: str,
    model: str,
    upstream: Upstream,
    canonical: Canonical,
    started: float,
) -> Response | None:
    """Request-stage inspection. Returns a 403 block response, or None to forward."""
    writer = gw.writer
    config = gw.config
    _emit_gaps(writer, ctx, canonical, "request")
    fail_closed = bool(canonical.uninspected) and config.uninspected_policy == "fail_closed"
    verdict = evaluate(
        ruleset,
        canonical,
        stage="request",
        mode=config.mode,
        excerpt_max_chars=config.excerpt_max_chars,
    )
    for error in verdict.rule_errors:
        writer.emit(rule_error_event(ctx, rule_id=error.rule_id, error=error.error))
    if fail_closed or verdict.blocked:
        event_id = writer.emit(
            verdict_event(
                ctx,
                stage="request",
                format=fmt,
                model=model,
                upstream=upstream.name,
                action="block",
                enforced=True,
                matches=list(verdict.matches),
            )
        )
        return _block_response(
            gw, ctx, fmt, event_id=event_id, rule_ids=[m.rule_id for m in verdict.matches],
            started=started,
        )
    if verdict.action != "allow":
        writer.emit(
            verdict_event(
                ctx,
                stage="request",
                format=fmt,
                model=model,
                upstream=upstream.name,
                action=verdict.action,
                enforced=False,
                matches=list(verdict.matches),
            )
        )
    return None


def _relay_nonstream(
    gw: Gateway,
    ruleset: Ruleset,
    ctx: RequestContext,
    fmt: str,
    model: str,
    upstream: Upstream,
    upstream_response: httpx.Response,
    content: bytes,
    relay: dict[str, str],
    started: float,
) -> Response:
    fmt_mod = _fmt(fmt)
    try:
        canonical = fmt_mod.parse_response(content)
    except Exception:
        # Unparseable upstream body: the whole body is uninspected content.
        # Flag it so `uninspected_content` (and `fail_closed`) can see it.
        canonical = Canonical(
            uninspected=(
                Gap(GAP_OTHER, "response.body", "upstream response body could not be parsed"),
            )
        )
    blocked = _response_stage(gw, ruleset, ctx, fmt, model, upstream, canonical)
    if blocked:
        return _block_response(
            gw, ctx, fmt, event_id=blocked[0], rule_ids=blocked[1], started=started
        )
    response = Response(
        content=content, status_code=upstream_response.status_code, headers=relay
    )
    response.headers["x-gateway-request-id"] = ctx.request_id
    gw.writer.emit(
        request_completed_event(
            ctx, outcome="allowed", status=upstream_response.status_code, latency_ms=_ms(started)
        )
    )
    return response


async def _enforce_stream(
    gw: Gateway,
    ruleset: Ruleset,
    ctx: RequestContext,
    fmt: str,
    model: str,
    upstream: Upstream,
    upstream_response: httpx.Response,
    relay: dict[str, str],
    started: float,
) -> Response:
    """Enforce mode: buffer, evaluate, then replay the exact chunks or block."""
    writer = gw.writer
    config = gw.config
    fmt_mod = _fmt(fmt)
    chunks: list[bytes] = []
    buffer = bytearray()
    overflow = False
    stream_error: int | None = None
    try:
        async for chunk in upstream_response.aiter_bytes():
            if overflow:
                continue  # the response will be blocked; hold no more memory
            chunks.append(chunk)
            buffer.extend(chunk)
            if len(buffer) > config.max_buffer_bytes:
                overflow = True
                buffer = buffer[: config.max_buffer_bytes]
                chunks = []  # drop the replay copy; the overflow blocks below
    except httpx.TimeoutException:
        stream_error = 504
    except httpx.HTTPError:
        stream_error = 502
    await upstream_response.aclose()

    if stream_error is not None:
        return _upstream_error(gw, ctx, fmt, stream_error, "upstream stream interrupted", started)

    if overflow:
        overflow_error = (
            f"stream exceeded max_buffer_bytes ({config.max_buffer_bytes}); response blocked"
        )
        writer.emit(
            rule_error_event(ctx, rule_id="stream-buffer-overflow", error=overflow_error)
        )
        event_id = writer.emit(
            verdict_event(
                ctx,
                stage="response",
                format=fmt,
                model=model,
                upstream=upstream.name,
                action="block",
                enforced=True,
                matches=[],
            )
        )
        return _block_response(gw, ctx, fmt, event_id=event_id, rule_ids=[], started=started)

    canonical = fmt_mod.extract_stream_text(bytes(buffer))
    blocked = _response_stage(gw, ruleset, ctx, fmt, model, upstream, canonical)
    if blocked:
        return _block_response(
            gw, ctx, fmt, event_id=blocked[0], rule_ids=blocked[1], started=started
        )

    async def replay() -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield chunk

    response = StreamingResponse(replay(), status_code=upstream_response.status_code, headers=relay)
    response.headers["x-gateway-request-id"] = ctx.request_id
    writer.emit(
        request_completed_event(
            ctx, outcome="allowed", status=upstream_response.status_code, latency_ms=_ms(started)
        )
    )
    return response


async def _monitor_tee(
    gw: Gateway,
    ruleset: Ruleset,
    ctx: RequestContext,
    fmt: str,
    model: str,
    upstream: Upstream,
    upstream_response: httpx.Response,
    started: float,
) -> AsyncIterator[bytes]:
    """Monitor mode (flag policy): relay every chunk immediately, inspect afterwards.

    Under ``fail_closed`` the buffered path is used instead, because a stream
    already relayed cannot be blocked.
    """
    writer = gw.writer
    config = gw.config
    fmt_mod = _fmt(fmt)
    buffer = bytearray()
    overflow = False
    stream_error: int | None = None
    try:
        async for chunk in upstream_response.aiter_bytes():
            yield chunk
            if not overflow:
                buffer.extend(chunk)
                if len(buffer) > config.max_buffer_bytes:
                    overflow = True
                    buffer = buffer[: config.max_buffer_bytes]
    except httpx.TimeoutException:
        stream_error = 504
    except httpx.HTTPError:
        stream_error = 502
    finally:
        await upstream_response.aclose()
        # The stream has fully relayed (or was interrupted): monitor mode can
        # no longer block, it only records what it observed.
        if overflow:
            writer.emit(
                uninspected_content_event(
                    ctx,
                    kind=GAP_STREAM_OVERFLOW,
                    location="stream",
                    detail=f"stream exceeded max_buffer_bytes ({config.max_buffer_bytes}); "
                    "only the accumulated prefix was inspected",
                    stage="response",
                )
            )
        if stream_error is not None:
            writer.emit(
                uninspected_content_event(
                    ctx,
                    kind=GAP_STREAM_ERROR,
                    location="stream",
                    detail=f"upstream stream interrupted ({stream_error}); remainder not inspected",
                    stage="response",
                )
            )
        canonical = fmt_mod.extract_stream_text(bytes(buffer))
        _response_stage(gw, ruleset, ctx, fmt, model, upstream, canonical)
        writer.emit(
            request_completed_event(
                ctx,
                outcome="upstream_error" if stream_error is not None else "allowed",
                status=upstream_response.status_code,
                latency_ms=_ms(started),
            )
        )


async def forward_chat(
    gw: Gateway,
    ruleset: Ruleset,
    ctx: RequestContext,
    *,
    fmt: str,
    path: str,
    model: str,
    upstream: Upstream,
    body: bytes,
    client_headers: dict[str, str],
    started: float,
    stream: bool = False,
) -> Response:
    """Run one chat request end to end (streaming flag selects the D9 strategy).

    ``ruleset`` is the reference captured once at request start (D12), so a
    hot reload mid-request does not change what this request is judged by.
    """
    config = gw.config
    fmt_mod = _fmt(fmt)

    # --- request stage -------------------------------------------------------
    canonical = fmt_mod.parse_request(body)
    block = _request_stage(gw, ruleset, ctx, fmt, model, upstream, canonical, started)
    if block is not None:
        return block

    # --- forward --------------------------------------------------------------
    headers = routing.forward_headers(client_headers)
    routing.inject_upstream_credential(headers, upstream)
    url = routing.upstream_url(upstream, path)
    client = gw.client_for(upstream)
    request = client.build_request("POST", url, content=body, headers=headers)
    try:
        upstream_response = await client.send(request, stream=True)
    except httpx.TimeoutException:
        return _upstream_error(gw, ctx, fmt, 504, "upstream request timed out", started)
    except httpx.HTTPError:
        return _upstream_error(gw, ctx, fmt, 502, "upstream unavailable", started)

    relay = routing.relay_response_headers(upstream_response.headers)
    # Non-streaming (or a non-2xx upstream error body) is handled whole.
    if not stream or upstream_response.status_code >= 400:
        try:
            content = await upstream_response.aread()
        except httpx.TimeoutException:
            await upstream_response.aclose()
            return _upstream_error(gw, ctx, fmt, 504, "upstream request timed out", started)
        except httpx.HTTPError:
            await upstream_response.aclose()
            return _upstream_error(gw, ctx, fmt, 502, "upstream unavailable", started)
        await upstream_response.aclose()
        return _relay_nonstream(
            gw, ruleset, ctx, fmt, model, upstream, upstream_response, content, relay, started
        )

    # Enforce mode buffers before relaying. ``fail_closed`` buffers too, even
    # in monitor mode: a streamed response cannot be blocked after the fact,
    # so the stream is buffered, evaluated, and only then relayed or blocked
    # (design D12: fail_closed blocks regardless of the global mode).
    if config.mode == "enforce" or config.uninspected_policy == "fail_closed":
        return await _enforce_stream(
            gw, ruleset, ctx, fmt, model, upstream, upstream_response, relay, started
        )

    response = StreamingResponse(
        _monitor_tee(gw, ruleset, ctx, fmt, model, upstream, upstream_response, started),
        status_code=upstream_response.status_code,
        headers=relay,
    )
    response.headers["x-gateway-request-id"] = ctx.request_id
    return response
