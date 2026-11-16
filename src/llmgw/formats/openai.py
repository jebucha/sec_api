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
    tool_call_segment,
)

TEXT_LIKE_PARTS = ("text",)
IMAGE_PARTS = ("image_url", "image")


def _openai_call_name_arguments(call: Any) -> tuple[str, object] | None:
    """``(name, arguments)`` of an OpenAI ``tool_calls`` entry, or None if
    the entry is malformed (non-object, or no ``function.name`` string)."""
    if not isinstance(call, dict):
        return None
    function = call.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    if not isinstance(name, str):
        return None
    return name, function.get("arguments")


def _collect_openai_tool_calls(
    tool_calls_field: Any, location: str, tool_calls: list[str], gaps: list[Gap]
) -> None:
    """Append one segment per well-formed ``tool_calls`` entry; gap the rest.

    Shared by the request and response parsers so both stages classify
    malformed tool content identically (non-list ``tool_calls`` is a
    ``tool_call`` gap).
    """
    if isinstance(tool_calls_field, list):
        for call_index, call in enumerate(tool_calls_field):
            pair = _openai_call_name_arguments(call)
            if pair is None:
                gaps.append(
                    Gap(
                        GAP_TOOL_CALL,
                        f"{location}.tool_calls[{call_index}]",
                        "malformed tool call entry; content was not inspected",
                    )
                )
            else:
                name, arguments = pair
                tool_calls.append(tool_call_segment(name, arguments))
    elif tool_calls_field:
        gaps.append(
            Gap(
                GAP_TOOL_CALL,
                f"{location}.tool_calls",
                "tool_calls is not a list; content was not inspected",
            )
        )


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
    reasoning: list[str] = []
    tool_calls: list[str] = []
    tool_results: list[str] = []
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
                # D2: string content is one tool_results segment verbatim;
                # non-string content stays a gap.
                content = message.get("content")
                if isinstance(content, str):
                    if content:
                        tool_results.append(content)
                elif content is not None:
                    gaps.append(
                        Gap(
                            GAP_TOOL_RESULT,
                            f"messages[{index}]",
                            "non-string tool result content; content was not inspected",
                        )
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
            reasoning_content = message.get("reasoning_content")
            if reasoning_content is not None:
                if isinstance(reasoning_content, str):
                    if reasoning_content:
                        reasoning.append(reasoning_content)
                else:
                    gaps.append(
                        Gap(
                            GAP_UNKNOWN_PART,
                            location,
                            "non-string reasoning_content; content was not inspected",
                        )
                    )
            tool_calls_field = message.get("tool_calls")
            _collect_openai_tool_calls(tool_calls_field, location, tool_calls, gaps)
            if message.get("function_call"):
                gaps.append(
                    Gap(
                        GAP_TOOL_CALL,
                        f"{location}.function_call",
                        "legacy function_call is not inspected",
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
        reasoning_text=tuple(reasoning),
        tool_calls=tuple(tool_calls),
        tool_results=tuple(tool_results),
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
    reasoning: list[str] = []
    tool_calls: list[str] = []
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
                            "legacy function_call is not inspected",
                        )
                    )
                continue
            if not isinstance(message, dict):
                gaps.append(Gap(GAP_OTHER, f"choices[{index}].message", "non-object message"))
                continue
            location = f"choices[{index}].message"
            reasoning_content = message.get("reasoning_content")
            if reasoning_content is not None:
                if isinstance(reasoning_content, str):
                    if reasoning_content:
                        reasoning.append(reasoning_content)
                else:
                    gaps.append(
                        Gap(
                            GAP_UNKNOWN_PART,
                            location,
                            "non-string reasoning_content; content was not inspected",
                        )
                    )
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
            tool_calls_field = message.get("tool_calls")
            _collect_openai_tool_calls(tool_calls_field, location, tool_calls, gaps)
            if message.get("function_call"):
                gaps.append(
                    Gap(GAP_TOOL_CALL, location, "legacy function_call is not inspected")
                )

    return Canonical(
        assistant_text=tuple(assistant),
        reasoning_text=tuple(reasoning),
        tool_calls=tuple(tool_calls),
        uninspected=tuple(gaps),
    )


def _accumulate_openai_fragment(
    fragment: Any,
    location: str,
    call_names: dict[int, str],
    call_args: dict[int, list[str]],
    open_indices: set[int],
    gaps: list[Gap],
) -> None:
    """Accumulate one ``delta.tool_calls`` fragment by index (design D4).

    Fragments that cannot be accumulated (non-object, missing index,
    non-string name or arguments) are recorded as gaps with a detail that
    never contains raw fragment content.
    """
    if not isinstance(fragment, dict):
        gaps.append(Gap(GAP_TOOL_CALL, location, "non-object tool call fragment"))
        return
    index = fragment.get("index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        gaps.append(Gap(GAP_TOOL_CALL, location, "tool call fragment without a usable index"))
        return
    open_indices.add(index)
    function = fragment.get("function")
    if function is None:
        return  # index-only fragment: the slot opens and later fragments fill it
    if not isinstance(function, dict):
        gaps.append(Gap(GAP_TOOL_CALL, location, "non-object function fragment"))
        return
    name = function.get("name")
    if name is not None:
        if isinstance(name, str):
            call_names.setdefault(index, name)
        else:
            gaps.append(Gap(GAP_TOOL_CALL, location, "non-string tool call name fragment"))
    arguments = function.get("arguments")
    if arguments is not None:
        if isinstance(arguments, str):
            call_args.setdefault(index, []).append(arguments)
        else:
            gaps.append(Gap(GAP_TOOL_CALL, location, "non-string arguments fragment"))


def _finalize_openai_calls(
    call_names: dict[int, str],
    call_args: dict[int, list[str]],
    open_indices: set[int],
    gaps: list[Gap],
) -> list[str]:
    """One D1 segment per reconstructed call, at stream end (design D4).

    An opened slot with nothing reconstructable (no name, no arguments) is
    a gap, never a silent drop.
    """
    segments: list[str] = []
    for index in sorted(open_indices):
        location = f"stream.tool_calls[{index}]"
        name = call_names.get(index)
        fragments = call_args.get(index, ())
        if name is None:
            detail = (
                "tool call fragments without a name; not reconstructable"
                if fragments
                else "tool call fragment without name or arguments; not reconstructable"
            )
            gaps.append(Gap(GAP_TOOL_CALL, location, detail))
            continue
        raw_arguments = "".join(fragments) if fragments else None
        segments.append(tool_call_segment(name, raw_arguments))
    return segments


def extract_stream_text(raw: bytes) -> Canonical:
    """Build the canonical structure from an accumulated OpenAI SSE stream.

    Assistant text comes from ``choices[].delta.content``; reasoning text is
    accumulated from ``choices[].delta.reasoning_content`` into one segment at
    stream end. Tool calls are reconstructed from
    ``choices[].delta.tool_calls`` fragments accumulated by index (design D4):
    one D1 segment per complete call at stream end.
    Protocol-only chunks (role, finish_reason) are ignored; chunks carrying
    uninspectable content (error objects, non-accumulatable tool-call
    fragments, other non-text delta fields) are recorded as uninspected.
    """
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    gaps: list[Gap] = []
    call_names: dict[int, str] = {}
    call_args: dict[int, list[str]] = {}
    open_indices: set[int] = set()
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
            reasoning_content = delta.get("reasoning_content")
            if isinstance(reasoning_content, str):
                reasoning_parts.append(reasoning_content)
            elif reasoning_content is not None:
                gaps.append(
                    Gap(
                        GAP_UNKNOWN_PART,
                        f"choices[{index}].delta.reasoning_content",
                        "non-string reasoning_content delta",
                    )
                )
            delta_tool_calls = delta.get("tool_calls")
            if delta_tool_calls is not None:
                if isinstance(delta_tool_calls, list):
                    fragment_location = f"choices[{index}].delta.tool_calls"
                    for fragment in delta_tool_calls:
                        _accumulate_openai_fragment(
                            fragment, fragment_location, call_names, call_args, open_indices, gaps
                        )
                else:
                    gaps.append(
                        Gap(
                            GAP_TOOL_CALL,
                            f"choices[{index}].delta.tool_calls",
                            "tool_calls delta is not a list; content was not inspected",
                        )
                    )
            non_text = {
                key for key in delta
                if key not in ("role", "content", "reasoning_content", "tool_calls")
                and delta[key] is not None
            }
            if non_text:
                kind = GAP_TOOL_CALL if "function_call" in non_text else GAP_UNKNOWN_PART
                gaps.append(
                    Gap(kind, f"choices[{index}].delta", "non-text delta fields present")
                )
    tool_calls = _finalize_openai_calls(call_names, call_args, open_indices, gaps)
    full_text = "".join(text_parts)
    full_reasoning = "".join(reasoning_parts)
    return Canonical(
        assistant_text=(full_text,) if full_text else (),
        reasoning_text=(full_reasoning,) if full_reasoning else (),
        tool_calls=tuple(tool_calls),
        uninspected=tuple(gaps),
    )


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
