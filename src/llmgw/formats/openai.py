"""OpenAI-compatible wire format: request/response parsing, SSE, error shapes."""

from __future__ import annotations

from typing import Any

from . import _wire
from ._wire import FormatError, iter_sse_json, json_dumps, parse_json_lenient, parse_json_strict
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

TEXT_LIKE_PARTS = ("text",)
IMAGE_PARTS = ("image_url", "image")


def _text_of_part(part: Any) -> str | None:
    """The text of a content part, or None if the part carries no text."""
    if isinstance(part, str):
        return part
    if not isinstance(part, dict):
        return None
    if part.get("type") in TEXT_LIKE_PARTS:
        text = part.get("text")
        return text if isinstance(text, str) else None
    return None


def _gap_kind_for_part(part: Any) -> tuple[str, str]:
    if not isinstance(part, dict):
        return GAP_UNKNOWN_PART, "non-object content part"
    ptype = part.get("type")
    if ptype in IMAGE_PARTS:
        return GAP_IMAGE, "image part"
    return GAP_UNKNOWN_PART, "unrecognized content part type"


def parse_request(body: bytes) -> Canonical:
    """Parse an OpenAI chat-completions request into the canonical structure.

    Raises FormatError for invalid JSON and DuplicateKeyError for duplicate
    keys (top level or inside any object, including messages).
    """
    try:
        data = parse_json_strict(body)
    except _wire.DuplicateKeyError:
        raise
    except Exception as exc:  # json.JSONDecodeError etc.
        raise FormatError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise FormatError("request body must be a JSON object")

    system: list[str] = []
    user: list[str] = []
    assistant: list[str] = []
    gaps: list[Gap] = []

    messages = data.get("messages")
    if messages is not None and not isinstance(messages, list):
        gaps.append(Gap(GAP_OTHER, "messages", "messages is not a list; content was not inspected"))
    if isinstance(messages, list):
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                gaps.append(Gap(GAP_UNKNOWN_PART, f"messages[{index}]", "non-object message"))
                continue
            role = message.get("role")
            if role == "tool":
                gaps.append(
                    Gap(GAP_TOOL_RESULT, f"messages[{index}]", "tool result is not inspected in v1")
                )
                continue
            if role not in ("system", "developer", "user", "assistant"):
                gaps.append(
                    Gap(GAP_UNKNOWN_PART, f"messages[{index}]", "unrecognized message role")
                )
                continue
            location = f"messages[{index}]"
            content = message.get("content")
            if content is not None:
                gaps.extend(_route_content(content, role, location, system, user, assistant))
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for call_index, _call in enumerate(tool_calls):
                    gaps.append(
                        Gap(
                            GAP_TOOL_CALL,
                            f"{location}.tool_calls[{call_index}]",
                            "assistant tool_calls are not inspected in v1",
                        )
                    )
            elif tool_calls:
                gaps.append(
                    Gap(
                        GAP_OTHER,
                        f"{location}.tool_calls",
                        "tool_calls is not a list; content was not inspected",
                    )
                )
            if message.get("function_call"):
                gaps.append(
                    Gap(
                        GAP_TOOL_CALL,
                        f"{location}.function_call",
                        "legacy function_call is not inspected in v1",
                    )
                )
    tools = data.get("tools")
    if isinstance(tools, list) and tools:
        gaps.append(
            Gap(GAP_TOOL_DEFINITIONS, "tools", "request tool definitions are not inspected in v1")
        )
    elif tools:
        gaps.append(Gap(GAP_OTHER, "tools", "tools is not a list; content was not inspected"))
    functions = data.get("functions")
    if isinstance(functions, list) and functions:
        gaps.append(
            Gap(
                GAP_TOOL_DEFINITIONS,
                "functions",
                "legacy request function definitions are not inspected in v1",
            )
        )
    elif functions:
        gaps.append(
            Gap(GAP_OTHER, "functions", "functions is not a list; content was not inspected")
        )

    return Canonical(
        system=tuple(system),
        user_text=tuple(user),
        assistant_text=tuple(assistant),
        uninspected=tuple(gaps),
    )


def _route_content(
    content: Any, role: str, location: str, system: list[str], user: list[str], assistant: list[str]
) -> list[Gap]:
    """Extract text from content into the role's bucket; record gaps."""
    gaps: list[Gap] = []
    if role in ("system", "developer"):
        bucket = system
    elif role == "user":
        bucket = user
    else:
        bucket = assistant
    if isinstance(content, str):
        if content:
            bucket.append(content)
        return gaps
    if not isinstance(content, list):
        if content is not None:
            gaps.append(Gap(GAP_UNKNOWN_PART, f"{location}.content", "non-object content"))
        return gaps
    for index, part in enumerate(content):
        text = _text_of_part(part)
        if text is not None:
            if text:
                bucket.append(text)
            continue
        kind, detail = _gap_kind_for_part(part)
        gaps.append(Gap(kind=kind, location=f"{location}.content[{index}]", detail=detail))
    return gaps


def parse_response(body: bytes) -> Canonical:
    """Parse an OpenAI chat-completions (non-streaming) response."""
    try:
        data = parse_json_lenient(body)
    except Exception as exc:
        raise FormatError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise FormatError("response body must be a JSON object")

    assistant: list[str] = []
    gaps: list[Gap] = []
    choices = data.get("choices")
    if choices is not None and not isinstance(choices, list):
        gaps.append(Gap(GAP_OTHER, "choices", "choices is not a list; content was not inspected"))
    if isinstance(choices, list):
        for index, choice in enumerate(choices):
            if not isinstance(choice, dict):
                gaps.append(Gap(GAP_OTHER, f"choices[{index}]", "non-object choice"))
                continue
            message = choice.get("message")
            if message is None:
                # A legacy top-level function_call still carries uninspected content.
                if choice.get("function_call"):
                    gaps.append(
                        Gap(
                            GAP_TOOL_CALL,
                            f"choices[{index}].function_call",
                            "legacy function_call is not inspected in v1",
                        )
                    )
                continue
            if not isinstance(message, dict):
                gaps.append(Gap(GAP_OTHER, f"choices[{index}].message", "non-object message"))
                continue
            location = f"choices[{index}].message"
            content = message.get("content")
            if isinstance(content, str):
                if content:
                    assistant.append(content)
            elif isinstance(content, list):
                for pindex, part in enumerate(content):
                    text = _text_of_part(part)
                    if text is not None:
                        if text:
                            assistant.append(text)
                        continue
                    kind, detail = _gap_kind_for_part(part)
                    gaps.append(
                        Gap(kind=kind, location=f"{location}.content[{pindex}]", detail=detail)
                    )
            elif content is not None:
                gaps.append(
                    Gap(GAP_UNKNOWN_PART, f"{location}.content", "non-text content")
                )
            if message.get("tool_calls"):
                gaps.append(
                    Gap(
                        GAP_TOOL_CALL,
                        f"{location}.tool_calls",
                        "assistant tool_calls are not inspected in v1",
                    )
                )
            if message.get("function_call"):
                gaps.append(
                    Gap(GAP_TOOL_CALL, location, "legacy function_call is not inspected in v1")
                )

    return Canonical(assistant_text=tuple(assistant), uninspected=tuple(gaps))


def extract_stream_text(raw: bytes) -> tuple[str, list[Gap]]:
    """Extract the concatenated text deltas from an OpenAI SSE stream.

    Text deltas come from ``choices[].delta.content``. Protocol-only chunks
    (role, finish_reason) are ignored; chunks carrying non-text delta content
    (e.g. tool_calls) or stream errors are recorded as uninspected.
    """
    text_parts: list[str] = []
    gaps: list[Gap] = []
    for _event, data in iter_sse_json(raw):
        if data is None:
            continue  # "[DONE]" and unparseable data
        if not isinstance(data, dict):
            gaps.append(Gap(GAP_OTHER, "stream", "non-object stream event"))
            continue
        if "choices" not in data:
            if "error" in data:
                gaps.append(Gap(GAP_STREAM_ERROR, "stream", "error object in stream"))
            else:
                gaps.append(Gap(GAP_STREAM_ERROR, "stream", "event without choices"))
            continue
        choices = data["choices"]
        if not isinstance(choices, list):
            gaps.append(
                Gap(GAP_OTHER, "choices", "choices is not a list; content was not inspected")
            )
            continue
        for index, choice in enumerate(choices):
            if not isinstance(choice, dict):
                gaps.append(Gap(GAP_OTHER, f"choices[{index}]", "non-object choice"))
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                gaps.append(Gap(GAP_OTHER, f"choices[{index}].delta", "non-object delta"))
                continue
            content = delta.get("content")
            if isinstance(content, str):
                text_parts.append(content)
            elif content is not None:
                gaps.append(
                    Gap(
                        GAP_UNKNOWN_PART,
                        f"choices[{index}].delta.content",
                        "non-string delta content",
                    )
                )
            non_text = {
                key for key in delta
                if key not in ("role", "content") and delta[key] is not None
            }
            if non_text:
                kind = (
                    GAP_TOOL_CALL
                    if "tool_calls" in non_text or "function_call" in non_text
                    else GAP_UNKNOWN_PART
                )
                gaps.append(
                    Gap(kind, f"choices[{index}].delta", "non-text delta fields present")
                )
    return "".join(text_parts), tuple(gaps)


# --- provider-shaped error bodies -------------------------------------------

_OPENAI_TYPES = {
    400: "invalid_request_error",
    401: "invalid_request_error",
    403: "permission_error",
    404: "invalid_request_error",
    413: "invalid_request_error",
    502: "server_error",
    504: "server_error",
}


def error_body(
    status: int,
    message: str,
    *,
    type_override: str | None = None,
    param: str | None = None,
    code: str | None = None,
) -> bytes:
    """An OpenAI-shaped error body for the given status code."""
    etype = type_override or _OPENAI_TYPES.get(status, "invalid_request_error")
    return json_dumps(
        {"error": {"message": message, "type": etype, "param": param, "code": code}}
    )


def parse_error(body: bytes) -> dict[str, Any]:
    """Parse an OpenAI error body back to a dict (for tests and the client)."""
    data = parse_json_lenient(body)
    if not isinstance(data, dict) or not isinstance(data.get("error"), dict):
        raise FormatError("not an OpenAI error body")
    return data
