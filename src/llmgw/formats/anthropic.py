"""Anthropic-compatible wire format: request/response parsing, SSE, errors."""

from __future__ import annotations

from typing import Any

from ._wire import (
    DuplicateKeyError,
    FormatError,
    iter_sse_json,
    json_dumps,
    parse_json_lenient,
    parse_json_strict,
)
from .canonical import (
    GAP_IMAGE,
    GAP_OTHER,
    GAP_STREAM_ERROR,
    GAP_TOOL_CALL,
    GAP_TOOL_DEFINITIONS,
    GAP_TOOL_RESULT,
    GAP_UNKNOWN_PART,
    Canonical,
    Gap,
)


def _gap_kind_for_block(block: Any) -> tuple[str, str]:
    if not isinstance(block, dict):
        return GAP_UNKNOWN_PART, "non-object block"
    btype = block.get("type")
    if btype == "tool_use":
        return GAP_TOOL_CALL, "tool_use block is not inspected in v1"
    if btype == "tool_result":
        return GAP_TOOL_RESULT, "tool_result block is not inspected in v1"
    if btype == "image":
        return GAP_IMAGE, "image block is not inspected in v1"
    return GAP_UNKNOWN_PART, "unrecognized content block type"


def _text_of_block(block: Any) -> str | None:
    if isinstance(block, dict) and block.get("type") == "text":
        text = block.get("text")
        return text if isinstance(text, str) else None
    return None


def _parse_blocks(
    content: Any, location: str, bucket: list[str]
) -> list[Gap]:
    """Extract text from Anthropic content (string or blocks array)."""
    gaps: list[Gap] = []
    if isinstance(content, str):
        if content:
            bucket.append(content)
        return gaps
    if not isinstance(content, list):
        if content is not None:
            gaps.append(Gap(GAP_UNKNOWN_PART, f"{location}.content", "non-object content"))
        return gaps
    for index, block in enumerate(content):
        text = _text_of_block(block)
        if text is not None:
            if text:
                bucket.append(text)
            continue
        kind, detail = _gap_kind_for_block(block)
        gaps.append(Gap(kind=kind, location=f"{location}.content[{index}]", detail=detail))
    return gaps


def parse_request(body: bytes) -> Canonical:
    """Parse an Anthropic /v1/messages request into the canonical structure.

    Top-level ``system`` (string or text blocks) goes to ``system``.
    Raises FormatError for invalid JSON and DuplicateKeyError for duplicate keys.
    """
    try:
        data = parse_json_strict(body)
    except DuplicateKeyError:
        raise
    except Exception as exc:
        raise FormatError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise FormatError("request body must be a JSON object")

    system: list[str] = []
    user: list[str] = []
    assistant: list[str] = []
    gaps: list[Gap] = []

    top_system = data.get("system")
    if isinstance(top_system, str):
        if top_system:
            system.append(top_system)
    elif isinstance(top_system, list):
        gaps.extend(_parse_blocks(top_system, "system", system))
    elif top_system is not None:
        gaps.append(
            Gap(GAP_OTHER, "system", "system is not a string or list of blocks; not inspected")
        )

    messages = data.get("messages")
    if messages is not None and not isinstance(messages, list):
        gaps.append(Gap(GAP_OTHER, "messages", "messages is not a list; content was not inspected"))
    if isinstance(messages, list):
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                gaps.append(Gap(GAP_UNKNOWN_PART, f"messages[{index}]", "non-object message"))
                continue
            role = message.get("role")
            if role not in ("user", "assistant"):
                gaps.append(
                    Gap(GAP_UNKNOWN_PART, f"messages[{index}]", "unrecognized message role")
                )
                continue
            location = f"messages[{index}]"
            content = message.get("content")
            if content is None:
                continue
            bucket = user if role == "user" else assistant
            gaps.extend(_parse_blocks(content, location, bucket))

    tools = data.get("tools")
    if isinstance(tools, list) and tools:
        gaps.append(
            Gap(GAP_TOOL_DEFINITIONS, "tools", "request tool definitions are not inspected in v1")
        )
    elif tools:
        gaps.append(
            Gap(GAP_OTHER, "tools", "tools is not a list; content was not inspected")
        )

    return Canonical(
        system=tuple(system),
        user_text=tuple(user),
        assistant_text=tuple(assistant),
        uninspected=tuple(gaps),
    )


def parse_response(body: bytes) -> Canonical:
    """Parse an Anthropic /v1/messages (non-streaming) response."""
    try:
        data = parse_json_lenient(body)
    except Exception as exc:
        raise FormatError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise FormatError("response body must be a JSON object")

    assistant: list[str] = []
    gaps: list[Gap] = []
    content = data.get("content")
    if content is not None and not isinstance(content, list):
        gaps.append(Gap(GAP_OTHER, "content", "content is not a list of blocks; not inspected"))
    if isinstance(content, list):
        for index, block in enumerate(content):
            text = _text_of_block(block)
            if text is not None:
                if text:
                    assistant.append(text)
                continue
            kind, detail = _gap_kind_for_block(block)
            gaps.append(Gap(kind=kind, location=f"content[{index}]", detail=detail))

    return Canonical(assistant_text=tuple(assistant), uninspected=tuple(gaps))


def extract_stream_text(raw: bytes) -> tuple[str, list[Gap]]:
    """Extract the concatenated text deltas from an Anthropic SSE stream.

    Text deltas come from ``content_block_delta.delta.text`` (text_delta).
    Protocol-only events (ping, message_stop) are ignored; events that carry
    content the parser does not inspect are recorded as uninspected.
    """
    text_parts: list[str] = []
    gaps: list[Gap] = []
    for event, data in iter_sse_json(raw):
        if data is None:
            continue
        if not isinstance(data, dict):
            gaps.append(Gap(GAP_OTHER, "stream", "non-object stream event"))
            continue
        if event == "content_block_delta":
            delta = data.get("delta")
            if not isinstance(delta, dict):
                gaps.append(Gap(GAP_OTHER, "content_block_delta", "non-object delta"))
                continue
            if delta.get("type") == "text_delta":
                text = delta.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
                else:
                    gaps.append(
                        Gap(GAP_OTHER, "content_block_delta", "non-string text delta")
                    )
                continue
            gaps.append(
                Gap(
                    GAP_UNKNOWN_PART,
                    "content_block_delta",
                    "non-text delta type",
                )
            )
            continue
        elif event == "content_block_start":
            block = data.get("content_block")
            if not isinstance(block, dict):
                gaps.append(
                    Gap(GAP_OTHER, "content_block_start", "non-object content_block")
                )
            elif block.get("type") != "text":
                kind, detail = _gap_kind_for_block(block)
                gaps.append(Gap(kind, "content_block_start", detail))
            continue
        elif event in ("ping", "message_stop", "message_delta", "message_start"):
            # Protocol events without inspectable content.
            continue
        elif event == "error":
            gaps.append(Gap(GAP_STREAM_ERROR, "stream", "error event in stream"))
            continue
        else:
            gaps.append(Gap(GAP_STREAM_ERROR, "stream", "unrecognized SSE event"))
    return "".join(text_parts), tuple(gaps)


# --- provider-shaped error bodies -------------------------------------------

_ANTHROPIC_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "invalid_request_error",
    502: "api_error",
    504: "api_error",
}


def error_body(status: int, message: str, *, type_override: str | None = None) -> bytes:
    """An Anthropic-shaped error body for the given status code."""
    etype = type_override or _ANTHROPIC_TYPES.get(status, "invalid_request_error")
    return json_dumps({"type": "error", "error": {"type": etype, "message": message}})


def parse_error(body: bytes) -> dict[str, Any]:
    """Parse an Anthropic error body back to a dict (for tests and the client)."""
    data = parse_json_lenient(body)
    if not isinstance(data, dict) or data.get("type") != "error":
        raise FormatError("not an Anthropic error body")
    if not isinstance(data.get("error"), dict):
        raise FormatError("not an Anthropic error body")
    return data
