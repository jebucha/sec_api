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
    tool_call_segment,
)


def _gap_kind_for_block(block: Any) -> tuple[str, str]:
    """The gap kind for a block the parser does not extract.

    ``tool_use`` blocks normally become ``tool_calls`` segments; this is only
    reached when the block is malformed or the context does not collect tool
    calls (e.g. a response body).
    """
    if not isinstance(block, dict):
        return GAP_UNKNOWN_PART, "non-object block"
    btype = block.get("type")
    if btype == "tool_use":
        return GAP_TOOL_CALL, "tool_use block is not inspected"
    if btype == "tool_result":
        return GAP_TOOL_RESULT, "tool_result block is not inspected"
    if btype == "image":
        return GAP_IMAGE, "image block is not inspected"
    return GAP_UNKNOWN_PART, "unrecognized content block type"


def _gap_kind_for_part(part: Any) -> tuple[str, str]:
    if not isinstance(part, dict):
        return GAP_UNKNOWN_PART, "non-object content part"
    if part.get("type") == "image":
        return GAP_IMAGE, "image part"
    return GAP_UNKNOWN_PART, "unrecognized content part type"


def _tool_result_segments(block: dict, location: str, out: list[str]) -> list[Gap]:
    """D2: one ``tool_results`` segment per text unit of a tool_result block;
    non-text parts (images, unknown) stay gaps."""
    content = block.get("content")
    if content is None:
        return []
    if isinstance(content, str):
        if content:
            out.append(content)
        return []
    if isinstance(content, list):
        gaps: list[Gap] = []
        for index, part in enumerate(content):
            part_location = f"{location}.content[{index}]"
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str):
                    if text:
                        out.append(text)
                    continue
            kind, detail = _gap_kind_for_part(part)
            gaps.append(Gap(kind=kind, location=part_location, detail=detail))
        return gaps
    return [
        Gap(
            GAP_TOOL_RESULT,
            location,
            "non-string tool_result content; content was not inspected",
        )
    ]


def _route_block(
    block: Any,
    location: str,
    bucket: list[str],
    tool_calls: list[str] | None,
    tool_results: list[str] | None,
) -> list[Gap]:
    """Route one content block: text to ``bucket``, well-formed tool blocks to
    their canonical fields, everything uninspected to a gap."""
    if isinstance(block, dict) and block.get("type") == "text":
        text = block.get("text")
        if isinstance(text, str):
            if text:
                bucket.append(text)
            return []
        return [Gap(GAP_UNKNOWN_PART, location, "non-string text block")]
    if isinstance(block, dict) and block.get("type") == "tool_use":
        if tool_calls is None:
            kind, detail = _gap_kind_for_block(block)
            return [Gap(kind, location, detail)]
        name = block.get("name")
        if isinstance(name, str):
            tool_calls.append(tool_call_segment(name, block.get("input")))
            return []
        return [
            Gap(GAP_TOOL_CALL, location, "malformed tool_use block; content was not inspected")
        ]
    if isinstance(block, dict) and block.get("type") == "tool_result":
        if tool_results is None:
            kind, detail = _gap_kind_for_block(block)
            return [Gap(kind, location, detail)]
        return _tool_result_segments(block, location, tool_results)
    kind, detail = _gap_kind_for_block(block)
    return [Gap(kind=kind, location=location, detail=detail)]


def _parse_blocks(
    content: Any,
    location: str,
    bucket: list[str],
    tool_calls: list[str] | None = None,
    tool_results: list[str] | None = None,
) -> list[Gap]:
    """Extract text from Anthropic content (string or blocks array); tool
    blocks route to ``tool_calls``/``tool_results`` when collectors are given."""
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
        gaps.extend(
            _route_block(block, f"{location}.content[{index}]", bucket, tool_calls, tool_results)
        )
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
    tool_calls: list[str] = []
    tool_results: list[str] = []
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
            gaps.extend(_parse_blocks(content, location, bucket, tool_calls, tool_results))

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
        tool_calls=tuple(tool_calls),
        tool_results=tuple(tool_results),
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
    tool_calls: list[str] = []
    gaps: list[Gap] = []
    content = data.get("content")
    if content is not None and not isinstance(content, list):
        gaps.append(Gap(GAP_OTHER, "content", "content is not a list of blocks; not inspected"))
    if isinstance(content, list):
        for index, block in enumerate(content):
            # Response stage: tool_use blocks are extracted; tool_result
            # blocks do not occur in assistant responses and stay a gap.
            gaps.extend(_route_block(block, f"content[{index}]", assistant, tool_calls, None))

    return Canonical(
        assistant_text=tuple(assistant), tool_calls=tuple(tool_calls), uninspected=tuple(gaps)
    )


def extract_stream_text(raw: bytes) -> Canonical:
    """Build the canonical structure from an accumulated Anthropic SSE stream.

    Assistant text comes from ``content_block_delta.delta.text`` (text_delta).
    ``tool_use`` blocks are reconstructed across ``content_block_start`` /
    ``input_json_delta`` / ``content_block_stop`` events (design D4): one D1
    segment per block. Protocol-only events (ping, message_stop) are ignored;
    events that carry content the parser does not inspect are recorded as
    uninspected.
    """
    text_parts: list[str] = []
    gaps: list[Gap] = []
    tool_call_segments: list[str] = []
    # Open tool_use slots keyed by block index:
    # {"name": str, "json": str, "input": object | None}
    tool_slots: dict[int, dict] = {}

    def close_slot(index: int) -> None:
        slot = tool_slots.pop(index, None)
        if slot is not None:
            # Accumulated fragments win; a provider that sends the complete
            # input at content_block_start (no input_json_delta) still yields
            # an inspectable segment instead of "input": null.
            arguments = slot["json"] or slot["input"]
            tool_call_segments.append(tool_call_segment(slot["name"], arguments))

    for event, data in iter_sse_json(raw):
        if data is None:
            continue
        if not isinstance(data, dict):
            gaps.append(Gap(GAP_OTHER, "stream", "non-object stream event"))
            continue
        if event == "content_block_start":
            block = data.get("content_block")
            if not isinstance(block, dict):
                gaps.append(Gap(GAP_OTHER, "content_block_start", "non-object content_block"))
                continue
            btype = block.get("type")
            if btype == "text":
                continue  # text arrives via text_delta events
            if btype == "tool_use":
                index = data.get("index")
                name = block.get("name")
                if (
                    isinstance(index, bool)
                    or not isinstance(index, int)
                    or index < 0
                    or not isinstance(name, str)
                ):
                    gaps.append(
                        Gap(
                            GAP_TOOL_CALL,
                            "content_block_start",
                            "malformed tool_use block start; content was not inspected",
                        )
                    )
                else:
                    initial = block.get("input")
                    tool_slots[index] = {
                        "name": name,
                        "json": "",
                        "input": initial if initial else None,
                    }
                continue
            kind, detail = _gap_kind_for_block(block)
            gaps.append(Gap(kind, "content_block_start", detail))
            continue
        if event == "content_block_delta":
            delta = data.get("delta")
            if not isinstance(delta, dict):
                gaps.append(Gap(GAP_OTHER, "content_block_delta", "non-object delta"))
                continue
            dtype = delta.get("type")
            if dtype == "text_delta":
                text = delta.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
                else:
                    gaps.append(
                        Gap(GAP_OTHER, "content_block_delta", "non-string text delta")
                    )
                continue
            if dtype == "input_json_delta":
                slot = tool_slots.get(data.get("index"))
                if slot is None:
                    gaps.append(
                        Gap(
                            GAP_UNKNOWN_PART,
                            "content_block_delta",
                            "input_json_delta without an open tool_use block",
                        )
                    )
                    continue
                partial = delta.get("partial_json")
                if isinstance(partial, str):
                    slot["json"] += partial
                else:
                    gaps.append(
                        Gap(GAP_OTHER, "content_block_delta", "non-string partial_json fragment")
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
        if event == "content_block_stop":
            close_slot(data.get("index"))
            continue
        if event in ("ping", "message_stop", "message_delta", "message_start"):
            # Protocol events without inspectable content.
            continue
        if event == "error":
            gaps.append(Gap(GAP_STREAM_ERROR, "stream", "error event in stream"))
            continue
        gaps.append(Gap(GAP_STREAM_ERROR, "stream", "unrecognized SSE event"))
    # A stream that ends without content_block_stop still yields what was
    # accumulated; the proxy adds the stream_error gap for the interruption.
    for index in sorted(tool_slots):
        close_slot(index)
    full_text = "".join(text_parts)
    return Canonical(
        assistant_text=(full_text,) if full_text else (),
        tool_calls=tuple(tool_call_segments),
        uninspected=tuple(gaps),
    )


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
