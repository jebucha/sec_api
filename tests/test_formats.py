"""Fixture tests for the OpenAI and Anthropic format adapters.

Covers T-CONTENT-NORMALIZATION-002..011 (both formats) plus 4.4 SSE extraction
and 4.5 error-body shapes.
"""

from __future__ import annotations

import json

import pytest

from llmgw.formats import anthropic, openai
from llmgw.formats._wire import DuplicateKeyError, FormatError
from llmgw.formats.canonical import (
    GAP_IMAGE,
    GAP_TOOL_CALL,
    GAP_TOOL_DEFINITIONS,
    GAP_TOOL_RESULT,
    GAP_UNKNOWN_PART,
    Canonical,
)


def _oai(body: dict | str) -> Canonical:
    raw = json.dumps(body).encode() if isinstance(body, dict) else body.encode()
    return openai.parse_request(raw)


def _ant(body: dict | str) -> Canonical:
    raw = json.dumps(body).encode() if isinstance(body, dict) else body.encode()
    return anthropic.parse_request(raw)


# --- system prompt extraction ------------------------------------------------

def test_openai_system_message_extracted():
    """T-CONTENT-NORMALIZATION-002"""
    c = _oai({"messages": [{"role": "system", "content": "You are terse."}]})
    assert c.system == ("You are terse.",)


def test_openai_developer_message_extracted():
    c = _oai({"messages": [{"role": "developer", "content": "Be brief."}]})
    assert c.system == ("Be brief.",)


def test_anthropic_system_blocks_extracted():
    """T-CONTENT-NORMALIZATION-003: array of text blocks, concatenated per block."""
    c = _ant({"system": [{"type": "text", "text": "You are "}, {"type": "text", "text": "terse."}]})
    assert c.system == ("You are ", "terse.")


def test_anthropic_system_string_extracted():
    c = _ant({"system": "You are terse."})
    assert c.system == ("You are terse.",)


# --- text extraction ----------------------------------------------------------

def test_multipart_user_content_extracted():
    """T-CONTENT-NORMALIZATION-004: OpenAI array content, Anthropic blocks."""
    oai = _oai(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "first part"},
                        {"type": "text", "text": "second part"},
                    ],
                }
            ]
        }
    )
    assert oai.user_text == ("first part", "second part")

    ant = _ant(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "first"},
                        {"type": "text", "text": "second"},
                    ],
                }
            ]
        }
    )
    assert ant.user_text == ("first", "second")


def test_string_content_extracted():
    oai = _oai({"messages": [{"role": "user", "content": "hello"}]})
    assert oai.user_text == ("hello",)
    ant = _ant({"messages": [{"role": "user", "content": "hello"}]})
    assert ant.user_text == ("hello",)


def test_prior_assistant_turns_in_assistant_text():
    """T-CONTENT-NORMALIZATION-005: earlier assistant messages are inspectable."""
    oai = _oai(
        {
            "messages": [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2"},
            ]
        }
    )
    assert oai.user_text == ("q1", "q2")
    assert oai.assistant_text == ("a1",)

    ant = _ant(
        {
            "messages": [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": [{"type": "text", "text": "a1"}]},
                {"role": "user", "content": "q2"},
            ]
        }
    )
    assert ant.assistant_text == ("a1",)


# --- response normalization ----------------------------------------------------

def test_response_text_in_assistant_text():
    oai_body = json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()
    ant_body = json.dumps({"content": [{"type": "text", "text": "hello"}]}).encode()
    assert openai.parse_response(oai_body).assistant_text == ("hello",)
    assert anthropic.parse_response(ant_body).assistant_text == ("hello",)


# --- duplicate JSON keys -------------------------------------------------------

def test_duplicate_top_level_key_rejected_400():
    """T-CONTENT-NORMALIZATION-007: two ``messages`` keys are rejected.

    The parser raises DuplicateKeyError, which the app maps to a provider-shaped
    400 without contacting the upstream (asserted app-level in 7.2).
    """
    body = b'{"model": "m", "messages": [1], "messages": [2]}'
    with pytest.raises(DuplicateKeyError, match="messages"):
        openai.parse_request(body)
    with pytest.raises(DuplicateKeyError, match="messages"):
        anthropic.parse_request(body)


def test_duplicate_message_key_rejected_400():
    """T-CONTENT-NORMALIZATION-008: two ``content`` keys in a message are rejected."""
    body = b'{"messages": [{"role": "user", "content": "a", "content": "b"}]}'
    with pytest.raises(DuplicateKeyError, match="content"):
        openai.parse_request(body)
    with pytest.raises(DuplicateKeyError, match="content"):
        anthropic.parse_request(body)


def test_invalid_json_raises_format_error():
    with pytest.raises(FormatError):
        openai.parse_request(b"{not json")
    with pytest.raises(FormatError):
        anthropic.parse_request(b"[1,2,3")


# --- uninspected content --------------------------------------------------------

def test_tool_blocks_emit_uninspected_content_event():
    """T-CONTENT-NORMALIZATION-009: tool_use/tool_result/tool_calls become gaps."""
    ant = _ant(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "calling a tool"},
                        {"type": "tool_use", "id": "t1", "name": "search", "input": {}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": "result text",
                        }
                    ],
                },
            ]
        }
    )
    kinds = [gap.kind for gap in ant.uninspected]
    assert GAP_TOOL_CALL in kinds
    assert GAP_TOOL_RESULT in kinds

    oai = _oai(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f"}}],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "out"},
            ]
        }
    )
    oai_kinds = [gap.kind for gap in oai.uninspected]
    assert GAP_TOOL_CALL in oai_kinds
    assert GAP_TOOL_RESULT in oai_kinds


def test_tool_definitions_emit_uninspected_content_event():
    """T-CONTENT-NORMALIZATION-010: non-empty ``tools`` array is a gap."""
    oai = _oai({"messages": [{"role": "user", "content": "hi"}], "tools": [{"type": "function"}]})
    assert any(gap.kind == GAP_TOOL_DEFINITIONS for gap in oai.uninspected)

    ant = _ant({"messages": [{"role": "user", "content": "hi"}], "tools": [{"name": "t"}]})
    assert any(gap.kind == GAP_TOOL_DEFINITIONS for gap in ant.uninspected)

    # Empty tools array is not a gap
    assert not _oai({"messages": [{"role": "user", "content": "hi"}], "tools": []}).uninspected


def test_image_part_recorded_uninspected():
    oai = _oai(
        {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]}
    )
    assert [gap.kind for gap in oai.uninspected] == [GAP_IMAGE]

    ant = _ant(
        {"messages": [{"role": "user", "content": [{"type": "image", "source": {}}]}]}
    )
    assert [gap.kind for gap in ant.uninspected] == [GAP_IMAGE]


def test_unknown_part_type_recorded_uninspected():
    """T-CONTENT-NORMALIZATION-011: unknown part type is a gap, parsing succeeds."""
    oai = _oai(
        {"messages": [{"role": "user", "content": [{"type": "mystery", "data": 1}]}]}
    )
    assert oai.user_text == ()
    assert [gap.kind for gap in oai.uninspected] == [GAP_UNKNOWN_PART]

    ant = _ant({"messages": [{"role": "user", "content": [{"type": "mystery"}]}]})
    assert [gap.kind for gap in ant.uninspected] == [GAP_UNKNOWN_PART]


def test_tool_call_gap_locations_are_positional():
    """VD-2: tool_calls gaps use positional indexes, never raw call content."""
    raw_args = "SECRET-RAW-ARGS-THAT-MUST-NOT-LEAK"
    oai = _oai(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "f", "arguments": raw_args},
                        },
                        {
                            "id": "c2",
                            "type": "function",
                            "function": {"name": "g", "arguments": "{}"},
                        },
                    ],
                }
            ]
        }
    )
    locations = [gap.location for gap in oai.uninspected if gap.kind == GAP_TOOL_CALL]
    assert locations == ["messages[0].tool_calls[0]", "messages[0].tool_calls[1]"]
    for gap in oai.uninspected:
        assert raw_args not in gap.detail


def test_legacy_function_call_and_functions_are_gaps():
    """VD-4: legacy ``function_call`` / ``functions`` are uninspected gaps."""
    oai = _oai(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "function_call": {"name": "f", "arguments": "{}"},
                }
            ]
        }
    )
    assert any(
        gap.kind == GAP_TOOL_CALL and gap.location == "messages[0].function_call"
        for gap in oai.uninspected
    )

    oai = _oai(
        {"messages": [{"role": "user", "content": "hi"}], "functions": [{"name": "f"}]}
    )
    assert any(
        gap.kind == GAP_TOOL_DEFINITIONS and gap.location == "functions"
        for gap in oai.uninspected
    )

    # response-side legacy function_call
    resp = openai.parse_response(
        b'{"choices":[{"message":{"role":"assistant","function_call":{"name":"f"}}}]}'
    )
    assert any(gap.kind == GAP_TOOL_CALL for gap in resp.uninspected)


def test_malformed_containers_are_gaps_not_crashes():
    """VD-11: non-list messages/tools/choices and non-dict entries are gaps."""
    c = _oai({"messages": "not a list"})
    assert any(gap.kind == "other" and gap.location == "messages" for gap in c.uninspected)

    c = _oai({"messages": [{"role": "user", "content": "hi"}, 42]})
    assert any(
        gap.kind == GAP_UNKNOWN_PART and gap.location == "messages[1]" for gap in c.uninspected
    )

    c = _oai({"messages": [{"role": "user", "content": "hi"}], "tools": {"not": "a list"}})
    assert any(gap.kind == "other" and gap.location == "tools" for gap in c.uninspected)

    c = _oai({"messages": [{"role": "user", "content": "hi"}], "functions": {"not": "a list"}})
    assert any(gap.kind == "other" and gap.location == "functions" for gap in c.uninspected)

    c = _oai({"messages": [{"role": "assistant", "content": None, "tool_calls": 42}]})
    assert any(
        gap.kind == "other" and gap.location == "messages[0].tool_calls"
        for gap in c.uninspected
    )

    c = _oai({"messages": [{"role": "user", "content": "hi"}]})
    assert not c.uninspected  # baseline still clean

    resp = openai.parse_response(b'{"choices":[{"message":{"content":42}}]}')
    assert any(
        gap.kind == GAP_UNKNOWN_PART and gap.location == "choices[0].message.content"
        for gap in resp.uninspected
    )

    resp = openai.parse_response(b'{"choices":{"not":"a list"}}')
    assert any(gap.kind == "other" and gap.location == "choices" for gap in resp.uninspected)

    resp = openai.parse_response(b'{"choices":[123, {"message": {"content": "ok"}}]}')
    assert any(gap.kind == "other" and gap.location == "choices[0]" for gap in resp.uninspected)
    assert resp.assistant_text == ("ok",)

    resp = openai.parse_response(b'{"choices":[{"message":"not an object"}]}')
    assert any(
        gap.kind == "other" and gap.location == "choices[0].message" for gap in resp.uninspected
    )

    # a choice without a message key is skipped, not a gap
    resp = openai.parse_response(b'{"choices":[{"index":0,"finish_reason":"stop"}]}')
    assert not resp.uninspected

    # anthropic: non-str system / non-list messages / non-list content
    c = _ant({"system": 42, "messages": [{"role": "user", "content": "hi"}]})
    assert any(gap.kind == "other" and gap.location == "system" for gap in c.uninspected)

    c = _ant({"messages": "not a list"})
    assert any(gap.kind == "other" and gap.location == "messages" for gap in c.uninspected)

    c = _ant({"messages": [{"role": "user", "content": "hi"}], "tools": {"not": "a list"}})
    assert any(gap.kind == "other" and gap.location == "tools" for gap in c.uninspected)

    resp = anthropic.parse_response(b'{"content":"not a list"}')
    assert any(gap.kind == "other" and gap.location == "content" for gap in resp.uninspected)


# --- SSE text-delta extraction (4.4) ---------------------------------------------

OAI_STREAM = (
    b'data: {"id":"1","choices":[{"delta":{"role":"assistant"}}]}\n\n'
    b'data: {"id":"1","choices":[{"delta":{"content":"Hel"}}]}\n\n'
    b'data: {"id":"1","choices":[{"delta":{"content":"lo"}}]}\n\n'
    b'data: {"id":"1","choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    b"data: [DONE]\n\n"
)

ANT_STREAM = (
    b'event: message_start\ndata: {"type":"message_start"}\n\n'
    b"event: content_block_start"
    b'\ndata: {"type":"content_block_start","content_block":{"type":"text","text":""}}\n\n'
    b"event: content_block_delta"
    b'\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hel"}}\n\n'
    b"event: content_block_delta"
    b'\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"lo"}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


def test_accumulated_stream_deltas_form_assistant_text():
    """T-CONTENT-NORMALIZATION-006 (both formats), incl. split chunk boundaries."""
    text, gaps = openai.extract_stream_text(OAI_STREAM)
    assert text == "Hello"
    assert gaps == ()

    text, gaps = anthropic.extract_stream_text(ANT_STREAM)
    assert text == "Hello"
    assert gaps == ()


def test_sse_extracts_across_chunk_boundaries_that_split_lines():
    # Feed the same stream split at arbitrary byte offsets; the accumulated
    # parse must yield the same result.
    for offset in (1, 5, 20, len(OAI_STREAM) // 2):
        chunks = [OAI_STREAM[:offset], OAI_STREAM[offset:]]
        text, _ = openai.extract_stream_text(b"".join(chunks))
        assert text == "Hello", offset
    for offset in (3, 17, 40):
        chunks = [ANT_STREAM[:offset], ANT_STREAM[offset : offset + 10], ANT_STREAM[offset + 10 :]]
        text, _ = anthropic.extract_stream_text(b"".join(chunks))
        assert text == "Hello", offset


def test_sse_non_text_deltas_are_gaps():
    oai_stream = (
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0}]}}]}\n\n'
        b'data: {"error":{"message":"boom"}}\n\n'
    )
    text, gaps = openai.extract_stream_text(oai_stream)
    assert text == ""
    kinds = [gap.kind for gap in gaps]
    assert GAP_TOOL_CALL in kinds
    assert any(gap.kind == "stream_error" for gap in gaps)

    nonstring_content = (
        b'data: {"choices":[{"delta":{"content":[{"type":"text","text":"hi"}]}}]}\n\n'
    )
    text, gaps = openai.extract_stream_text(nonstring_content)
    assert text == ""
    assert any(
        gap.kind == GAP_UNKNOWN_PART and gap.location == "choices[0].delta.content"
        for gap in gaps
    )

    ant_stream = (
        b"event: content_block_delta"
        b'\ndata: {"type":"content_block_delta","delta":'
        b'{"type":"input_json_delta","partial_json":"{}"}}\n\n'
        b'event: error\ndata: {"type":"error","error":{"type":"api_error","message":"x"}}\n\n'
    )
    text, gaps = anthropic.extract_stream_text(ant_stream)
    assert text == ""
    assert any(gap.kind == GAP_UNKNOWN_PART for gap in gaps)
    assert any(gap.kind == "stream_error" for gap in gaps)


def test_gap_details_never_contain_raw_values():
    """VD-2: gap detail is a description, never attacker-controlled raw text."""
    secret = "SECRET-RAW-TYPE-0123456789"
    openai_cases = [
        _oai({"messages": [{"role": "user", "content": [{"type": secret}]}]}),
        _oai({"messages": [{"role": secret, "content": "hi"}]}),
        _oai({"messages": [{"role": "assistant", "content": None, "tool_calls": 42}]}),
    ]
    ant_cases = [
        _ant({"messages": [{"role": "user", "content": [{"type": secret}]}]}),
        _ant({"messages": [{"role": secret, "content": "hi"}]}),
    ]
    for canonical in [*openai_cases, *ant_cases]:
        for gap in canonical.uninspected:
            assert secret not in gap.detail
            assert len(gap.detail) <= 200

    stream = f'event: {secret}\ndata: {{"type":"x"}}\n\n'.encode()
    _, openai_stream_gaps = openai.extract_stream_text(stream)
    _, ant_stream_gaps = anthropic.extract_stream_text(stream)
    for gap in [*openai_stream_gaps, *ant_stream_gaps]:
        assert secret not in gap.detail


# --- provider-shaped error bodies (4.5) -------------------------------------------

ERROR_STATUSES = (400, 401, 403, 404, 413, 502, 504)


@pytest.mark.parametrize("status", ERROR_STATUSES)
def test_openai_error_body_shape(status):
    body = openai.error_body(status, "it broke")
    data = openai.parse_error(body)
    err = data["error"]
    assert isinstance(err["message"], str)
    assert isinstance(err["type"], str)
    assert err["param"] is None or isinstance(err["param"], str)
    assert err["code"] is None or isinstance(err["code"], str)


@pytest.mark.parametrize("status", ERROR_STATUSES)
def test_anthropic_error_body_shape(status):
    body = anthropic.error_body(status, "it broke")
    data = anthropic.parse_error(body)
    assert data["type"] == "error"
    assert isinstance(data["error"]["type"], str)
    assert isinstance(data["error"]["message"], str)


def test_block_error_type_is_gateway_blocked_in_both_formats():
    oai = openai.parse_error(openai.error_body(403, "blocked", type_override="gateway_blocked"))
    assert oai["error"]["type"] == "gateway_blocked"
    ant = anthropic.parse_error(
        anthropic.error_body(403, "blocked", type_override="gateway_blocked")
    )
    assert ant["error"]["type"] == "gateway_blocked"
