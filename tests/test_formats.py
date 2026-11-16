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
    tool_call_segment,
    tool_definition_segment,
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


# --- reasoning content extraction ---------------------------------------------


def test_openai_response_reasoning_content_extracted():
    """T-CONTENT-NORMALIZATION-025: message.reasoning_content is a segment."""
    body = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "The answer is 4.",
                        "reasoning_content": "I should add 2 and 2.",
                    }
                }
            ]
        }
    ).encode()
    c = openai.parse_response(body)
    assert c.assistant_text == ("The answer is 4.",)
    assert c.reasoning_text == ("I should add 2 and 2.",)
    assert c.uninspected == ()


def test_openai_stream_reasoning_deltas_accumulated():
    """T-CONTENT-NORMALIZATION-026: delta.reasoning_content fragments join into
    one segment and are not recorded as non-text delta gaps."""
    stream = (
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        b'data: {"choices":[{"delta":{"reasoning_content":"Let me "}}]}\n\n'
        b'data: {"choices":[{"delta":{"reasoning_content":"think about it."}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"The answer is 4."}}]}\n\n'
        b'data: [DONE]\n\n'
    )
    c = openai.extract_stream_text(stream)
    assert c.assistant_text == ("The answer is 4.",)
    assert c.reasoning_text == ("Let me think about it.",)
    assert c.uninspected == ()


def test_openai_response_reasoning_alias_extracted():
    """T-CONTENT-NORMALIZATION-034: message.reasoning (no reasoning_content) is
    a reasoning segment, not an uninspected gap."""
    body = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "The answer is 4.",
                        "reasoning": "I should add 2 and 2.",
                    }
                }
            ]
        }
    ).encode()
    c = openai.parse_response(body)
    assert c.assistant_text == ("The answer is 4.",)
    assert c.reasoning_text == ("I should add 2 and 2.",)
    assert c.uninspected == ()


def test_openai_stream_reasoning_alias_accumulated():
    """T-CONTENT-NORMALIZATION-035: delta.reasoning fragments join into one
    segment and are not recorded as non-text delta gaps."""
    stream = (
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        b'data: {"choices":[{"delta":{"reasoning":"Let me "}}]}\n\n'
        b'data: {"choices":[{"delta":{"reasoning":"think about it."}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"The answer is 4."}}]}\n\n'
        b'data: [DONE]\n\n'
    )
    c = openai.extract_stream_text(stream)
    assert c.assistant_text == ("The answer is 4.",)
    assert c.reasoning_text == ("Let me think about it.",)
    assert c.uninspected == ()


def test_mirrored_reasoning_fields_not_duplicated():
    """T-CONTENT-NORMALIZATION-036: both fields carrying the same text yield
    exactly one segment (the reasoning_content value) and no uninspected event."""
    body = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "The answer is 4.",
                        "reasoning_content": "I should add 2 and 2.",
                        "reasoning": "I should add 2 and 2.",
                    }
                }
            ]
        }
    ).encode()
    c = openai.parse_response(body)
    assert c.reasoning_text == ("I should add 2 and 2.",)
    assert c.uninspected == ()

    stream = (
        b'data: {"choices":[{"delta":'
        b'{"reasoning_content":"Let me ","reasoning":"Let me "}}]}\n\n'
        b'data: {"choices":[{"delta":'
        b'{"reasoning_content":"think about it.","reasoning":"think about it."}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"The answer is 4."}}]}\n\n'
        b'data: [DONE]\n\n'
    )
    c = openai.extract_stream_text(stream)
    assert c.reasoning_text == ("Let me think about it.",)
    assert c.uninspected == ()


def test_malformed_reasoning_content_recorded_as_gap():
    """T-CONTENT-NORMALIZATION-033: non-string reasoning text (reasoning_content
    or the reasoning alias, streamed or non-streamed) is an unknown_part gap at
    the message/block/delta location; the rest parses normally."""
    oai = _oai(
        {
            "messages": [
                {"role": "assistant", "content": "answer", "reasoning_content": 42}
            ]
        }
    )
    assert oai.assistant_text == ("answer",)
    assert oai.reasoning_text == ()
    assert [(gap.kind, gap.location) for gap in oai.uninspected] == [
        (GAP_UNKNOWN_PART, "messages[0]")
    ]

    # non-streaming response: a non-string reasoning alias is a gap
    oai_resp = openai.parse_response(
        json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "answer",
                            "reasoning": 42,
                        }
                    }
                ]
            }
        ).encode()
    )
    assert oai_resp.assistant_text == ("answer",)
    assert oai_resp.reasoning_text == ()
    assert [(gap.kind, gap.location) for gap in oai_resp.uninspected] == [
        (GAP_UNKNOWN_PART, "choices[0].message")
    ]

    # streamed: a non-string reasoning delta is a gap
    oai_stream = openai.extract_stream_text(
        b'data: {"choices":[{"delta":{"reasoning":42}}]}\n\n'
    )
    assert oai_stream.reasoning_text == ()
    assert [(gap.kind, gap.location) for gap in oai_stream.uninspected] == [
        (GAP_UNKNOWN_PART, "choices[0].delta.reasoning")
    ]

    ant = anthropic.parse_response(
        json.dumps(
            {
                "content": [
                    {"type": "thinking", "thinking": 42},
                    {"type": "text", "text": "answer"},
                ]
            }
        ).encode()
    )
    assert ant.assistant_text == ("answer",)
    assert ant.reasoning_text == ()
    assert [(gap.kind, gap.location) for gap in ant.uninspected] == [
        (GAP_UNKNOWN_PART, "content[0]")
    ]


def test_anthropic_response_thinking_blocks_extracted():
    """T-CONTENT-NORMALIZATION-027: one reasoning segment per thinking block."""
    body = json.dumps(
        {
            "content": [
                {"type": "thinking", "thinking": "First thought."},
                {"type": "text", "text": "The answer is 4."},
                {"type": "thinking", "thinking": "Second thought."},
            ]
        }
    ).encode()
    c = anthropic.parse_response(body)
    assert c.assistant_text == ("The answer is 4.",)
    assert c.reasoning_text == ("First thought.", "Second thought.")
    assert c.uninspected == ()


def test_anthropic_stream_thinking_reconstructed():
    """T-CONTENT-NORMALIZATION-028: thinking deltas join into one segment;
    signature_delta is protocol-only and produces no gap."""
    stream = (
        b"event: content_block_start\n"
        b'data: {"type":"content_block_start","index":0,"content_block":'
        b'{"type":"thinking","thinking":""}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","index":0,"delta":'
        b'{"type":"thinking_delta","thinking":"Let me "}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","index":0,"delta":'
        b'{"type":"thinking_delta","thinking":"think about it."}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","index":0,"delta":'
        b'{"type":"signature_delta","signature":"c2lnbmF0dXJl"}}\n\n'
        b"event: content_block_stop\n"
        b'data: {"type":"content_block_stop","index":0}\n\n'
        b"event: content_block_start\n"
        b'data: {"type":"content_block_start","index":1,"content_block":'
        b'{"type":"text","text":""}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","index":1,"delta":'
        b'{"type":"text_delta","text":"The answer is 4."}}\n\n'
        b"event: content_block_stop\n"
        b'data: {"type":"content_block_stop","index":1}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    c = anthropic.extract_stream_text(stream)
    assert c.assistant_text == ("The answer is 4.",)
    assert c.reasoning_text == ("Let me think about it.",)
    assert c.uninspected == ()


# --- tool-call segment builder (1.1) ------------------------------------------

def test_tool_call_segment_name_and_object():
    """1.1: one JSON object per call with the call's name and parsed input."""
    segment = tool_call_segment("shell", '{"command": "ls", "count": 2}')
    assert json.loads(segment) == {"input": {"command": "ls", "count": 2}, "name": "shell"}
    # Anthropic-style input objects pass through unchanged
    assert tool_call_segment("shell", {"command": "ls", "count": 2}) == segment


def test_tool_call_segment_serialization_is_deterministic():
    """1.1: sort_keys serialization, stable regardless of argument key order."""
    a = tool_call_segment("shell", '{"command": "ls", "count": 2}')
    b = tool_call_segment("shell", '{"count": 2, "command": "ls"}')
    assert a == b == '{"input": {"command": "ls", "count": 2}, "name": "shell"}'


def test_tool_call_segment_unparseable_arguments_fallback():
    """1.1: a raw arguments string that does not parse stays the input."""
    raw = "not-json: {oops"
    segment = tool_call_segment("shell", raw)
    assert json.loads(segment) == {"input": raw, "name": "shell"}


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

def test_tool_blocks_no_longer_emit_uninspected_events():
    """T-CONTENT-NORMALIZATION-009: well-formed tool content is inspected, not gapped."""
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
    assert not any(
        gap.kind in (GAP_TOOL_CALL, GAP_TOOL_RESULT) for gap in ant.uninspected
    )
    assert ant.assistant_text == ("calling a tool",)
    assert ant.tool_calls == (tool_call_segment("search", {}),)
    assert ant.tool_results == ("result text",)

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
    assert not any(
        gap.kind in (GAP_TOOL_CALL, GAP_TOOL_RESULT) for gap in oai.uninspected
    )
    assert oai.tool_calls == (tool_call_segment("f", None),)
    assert oai.tool_results == ("out",)


def test_openai_request_tool_calls_extracted():
    """T-CONTENT-NORMALIZATION-016: one tool_calls segment per entry."""
    c = _oai(
        {
            "messages": [
                {"role": "user", "content": "ls the repo"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "shell", "arguments": '{"command": "ls"}'},
                        },
                        {
                            "id": "c2",
                            "type": "function",
                            "function": {"name": "read", "arguments": '{"path": "a.txt"}'},
                        },
                    ],
                },
            ]
        }
    )
    assert c.tool_calls == (
        tool_call_segment("shell", '{"command": "ls"}'),
        tool_call_segment("read", '{"path": "a.txt"}'),
    )
    assert not c.uninspected


def test_anthropic_request_tool_use_extracted():
    """T-CONTENT-NORMALIZATION-017: one tool_calls segment per tool_use block."""
    c = _ant(
        {
            "messages": [
                {"role": "user", "content": "ls the repo"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "shell",
                            "input": {"command": "ls"},
                        },
                        {
                            "type": "tool_use",
                            "id": "t2",
                            "name": "read",
                            "input": {"path": "a.txt"},
                        },
                    ],
                },
            ]
        }
    )
    assert c.tool_calls == (
        tool_call_segment("shell", {"command": "ls"}),
        tool_call_segment("read", {"path": "a.txt"}),
    )
    assert not c.uninspected


def test_openai_tool_result_messages_extracted():
    """T-CONTENT-NORMALIZATION-021: string content of role: tool messages is a segment."""
    c = _oai(
        {
            "messages": [
                {"role": "tool", "tool_call_id": "c1", "content": "file one"},
                {"role": "tool", "tool_call_id": "c2", "content": "file two"},
            ]
        }
    )
    assert c.tool_results == ("file one", "file two")
    assert not c.uninspected


def test_anthropic_tool_result_blocks_extracted():
    """T-CONTENT-NORMALIZATION-022: text parts are segments, image parts stay gaps."""
    c = _ant(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": "plain result",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "t2",
                            "content": [
                                {"type": "text", "text": "part one"},
                                {"type": "image", "source": {}},
                                {"type": "text", "text": "part two"},
                            ],
                        },
                    ],
                }
            ]
        }
    )
    assert c.tool_results == ("plain result", "part one", "part two")
    assert [gap.kind for gap in c.uninspected] == [GAP_IMAGE]


def test_response_tool_calls_extracted():
    """T-CONTENT-NORMALIZATION-018: response-stage tool calls, both formats."""
    oai = openai.parse_response(
        b'{"choices":[{"message":{"content":null,"tool_calls":'
        b'[{"id":"c1","type":"function",'
        b'"function":{"name":"shell","arguments":"{\\"command\\": \\"ls\\"}"}}]}}]}'
    )
    assert oai.tool_calls == (tool_call_segment("shell", {"command": "ls"}),)
    assert not oai.uninspected

    ant = anthropic.parse_response(
        b'{"content":[{"type":"text","text":"calling"},'
        b'{"type":"tool_use","id":"t1","name":"shell","input":{"command":"ls"}}]}'
    )
    assert ant.assistant_text == ("calling",)
    assert ant.tool_calls == (tool_call_segment("shell", {"command": "ls"}),)
    assert not ant.uninspected


def test_tool_definitions_extracted_without_uninspected_event():
    """T-CONTENT-NORMALIZATION-010: a non-empty ``tools`` array is extracted into
    ``tool_definitions``, not recorded as uninspected."""
    oai = _oai(
        {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "shell",
                        "description": "Run a command",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        }
    )
    assert oai.tool_definitions == (
        tool_definition_segment("shell", "Run a command", {"type": "object"}),
    )
    assert not any(gap.kind == GAP_TOOL_DEFINITIONS for gap in oai.uninspected)

    ant = _ant(
        {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "name": "t",
                    "description": "A tool",
                    "input_schema": {"type": "object"},
                }
            ],
        }
    )
    assert ant.tool_definitions == (
        tool_definition_segment("t", "A tool", {"type": "object"}),
    )
    assert not any(gap.kind == GAP_TOOL_DEFINITIONS for gap in ant.uninspected)

    # Empty tools array is not a gap
    assert not _oai({"messages": [{"role": "user", "content": "hi"}], "tools": []}).uninspected


def test_openai_tool_definitions_extracted():
    """T-CONTENT-NORMALIZATION-037: one segment per ``function``-type entry,
    carrying name, description, and parameter schema in a single JSON value."""
    c = _oai(
        {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "shell",
                        "description": "Run a command",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                        },
                    },
                },
                {"type": "function", "function": {"name": "read"}},
            ],
        }
    )
    assert c.tool_definitions == (
        tool_definition_segment(
            "shell",
            "Run a command",
            {"type": "object", "properties": {"command": {"type": "string"}}},
        ),
        tool_definition_segment("read", None, None),
    )
    assert not c.uninspected


def test_anthropic_tool_definitions_extracted():
    """T-CONTENT-NORMALIZATION-038: one segment per entry, carrying name,
    description, and input_schema in a single JSON value."""
    c = _ant(
        {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "name": "shell",
                    "description": "Run a command",
                    "input_schema": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                    },
                },
                {"name": "read"},
            ],
        }
    )
    assert c.tool_definitions == (
        tool_definition_segment(
            "shell",
            "Run a command",
            {"type": "object", "properties": {"command": {"type": "string"}}},
        ),
        tool_definition_segment("read", None, None),
    )
    assert not c.uninspected


def test_legacy_openai_functions_extracted():
    """T-CONTENT-NORMALIZATION-039: legacy top-level ``functions`` entries are
    extracted as segments, not recorded as uninspected."""
    c = _oai(
        {
            "messages": [{"role": "user", "content": "hi"}],
            "functions": [
                {
                    "name": "shell",
                    "description": "Run a command",
                    "parameters": {"type": "object"},
                }
            ],
        }
    )
    assert c.tool_definitions == (
        tool_definition_segment("shell", "Run a command", {"type": "object"}),
    )
    assert not c.uninspected


def test_malformed_tool_definition_recorded_as_gap():
    """T-CONTENT-NORMALIZATION-042: a malformed entry is a positional
    ``tool_definitions`` gap; well-formed siblings are still extracted."""
    c = _oai(
        {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                "not-an-object",
                {"type": "function"},  # no function object
                {"type": "custom", "function": {"name": "x"}},  # not function-typed
                {"type": "function", "function": {"name": "shell"}},
            ],
        }
    )
    assert c.tool_definitions == (tool_definition_segment("shell", None, None),)
    assert [(gap.kind, gap.location) for gap in c.uninspected] == [
        (GAP_TOOL_DEFINITIONS, "tools[0]"),
        (GAP_TOOL_DEFINITIONS, "tools[1]"),
        (GAP_TOOL_DEFINITIONS, "tools[2]"),
    ]

    ant = _ant(
        {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": ["not-an-object", {"name": "shell"}],
        }
    )
    assert ant.tool_definitions == (tool_definition_segment("shell", None, None),)
    assert [(gap.kind, gap.location) for gap in ant.uninspected] == [
        (GAP_TOOL_DEFINITIONS, "tools[0]")
    ]


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
    """VD-2: malformed tool content gaps use positional indexes, never raw call
    content. Well-formed entries are inspected segments, not gaps."""
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
                        42,  # non-object entry: a gap at its positional index
                    ],
                }
            ]
        }
    )
    assert oai.tool_calls == (tool_call_segment("f", raw_args),)  # well-formed entry inspected
    locations = [gap.location for gap in oai.uninspected if gap.kind == GAP_TOOL_CALL]
    assert locations == ["messages[0].tool_calls[1]"]
    for gap in oai.uninspected:
        assert raw_args not in gap.detail

    # non-list tool_calls: a tool_call gap without per-entry indexing
    oai = _oai({"messages": [{"role": "assistant", "content": None, "tool_calls": 42}]})
    assert any(
        gap.kind == GAP_TOOL_CALL and gap.location == "messages[0].tool_calls"
        for gap in oai.uninspected
    )


def test_legacy_function_call_and_malformed_functions_are_gaps():
    """VD-4: legacy ``function_call`` is an uninspected gap; a malformed legacy
    ``functions`` entry is a positional gap (well-formed entries are extracted)."""
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
        {"messages": [{"role": "user", "content": "hi"}], "functions": [42]}
    )
    assert any(
        gap.kind == GAP_TOOL_DEFINITIONS and gap.location == "functions[0]"
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
        gap.kind == GAP_TOOL_CALL and gap.location == "messages[0].tool_calls"
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
    oai = openai.extract_stream_text(OAI_STREAM)
    assert oai.assistant_text == ("Hello",)
    assert oai.uninspected == ()

    ant = anthropic.extract_stream_text(ANT_STREAM)
    assert ant.assistant_text == ("Hello",)
    assert ant.uninspected == ()


def test_sse_extracts_across_chunk_boundaries_that_split_lines():
    # Feed the same stream split at arbitrary byte offsets; the accumulated
    # parse must yield the same result.
    for offset in (1, 5, 20, len(OAI_STREAM) // 2):
        chunks = [OAI_STREAM[:offset], OAI_STREAM[offset:]]
        oai = openai.extract_stream_text(b"".join(chunks))
        assert oai.assistant_text == ("Hello",), offset
    for offset in (3, 17, 40):
        chunks = [ANT_STREAM[:offset], ANT_STREAM[offset : offset + 10], ANT_STREAM[offset + 10 :]]
        ant = anthropic.extract_stream_text(b"".join(chunks))
        assert ant.assistant_text == ("Hello",), offset


def test_sse_non_text_deltas_are_gaps():
    oai_stream = (
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0}]}}]}\n\n'
        b'data: {"error":{"message":"boom"}}\n\n'
    )
    oai = openai.extract_stream_text(oai_stream)
    assert oai.assistant_text == ()
    kinds = [gap.kind for gap in oai.uninspected]
    assert GAP_TOOL_CALL in kinds
    assert any(gap.kind == "stream_error" for gap in oai.uninspected)

    nonstring_content = (
        b'data: {"choices":[{"delta":{"content":[{"type":"text","text":"hi"}]}}]}\n\n'
    )
    oai = openai.extract_stream_text(nonstring_content)
    assert oai.assistant_text == ()
    assert any(
        gap.kind == GAP_UNKNOWN_PART and gap.location == "choices[0].delta.content"
        for gap in oai.uninspected
    )

    ant_stream = (
        b"event: content_block_delta"
        b'\ndata: {"type":"content_block_delta","delta":'
        b'{"type":"input_json_delta","partial_json":"{}"}}\n\n'
        b'event: error\ndata: {"type":"error","error":{"type":"api_error","message":"x"}}\n\n'
    )
    ant = anthropic.extract_stream_text(ant_stream)
    assert ant.assistant_text == ()
    assert any(gap.kind == GAP_UNKNOWN_PART for gap in ant.uninspected)
    assert any(gap.kind == "stream_error" for gap in ant.uninspected)


def test_openai_stream_tool_calls_reconstructed():
    """T-CONTENT-NORMALIZATION-019: one call split across chunks by index."""
    stream = (
        b'data: {"id":"1","choices":[{"delta":{"role":"assistant"}}]}\n\n'
        b'data: {"id":"1","choices":[{"delta":{"content":"running "}}]}\n\n'
        b'data: {"id":"1","choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
        b'"type":"function","function":{"name":"shell","arguments":"{\\"com"}}]}}]}\n\n'
        b'data: {"id":"1","choices":[{"delta":{"tool_calls":'
        b'[{"index":0,"function":{"arguments":"mand\\": \\"ls\\"}"}}]}}]}\n\n'
        b'data: {"id":"1","choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        b"data: [DONE]\n\n"
    )
    c = openai.extract_stream_text(stream)
    assert c.assistant_text == ("running ",)
    assert c.tool_calls == (tool_call_segment("shell", {"command": "ls"}),)
    assert c.uninspected == ()


def test_anthropic_stream_tool_use_reconstructed():
    """T-CONTENT-NORMALIZATION-020: tool_use block via start/delta/stop events."""
    stream = (
        b'event: message_start\ndata: {"type":"message_start"}\n\n'
        b"event: content_block_start\n"
        b'data: {"type":"content_block_start","index":0,"content_block":'
        b'{"type":"tool_use","id":"t1","name":"shell","input":{}}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","index":0,"delta":'
        b'{"type":"input_json_delta","partial_json":"{\\"com"}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","index":0,"delta":'
        b'{"type":"input_json_delta","partial_json":"mand\\": \\"ls\\"}"}}\n\n'
        b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )
    c = anthropic.extract_stream_text(stream)
    assert c.assistant_text == ()
    assert c.tool_calls == (tool_call_segment("shell", {"command": "ls"}),)
    assert c.uninspected == ()


def test_anthropic_stream_tool_use_complete_input_at_start():
    """A tool_use block whose full input arrives at content_block_start (no
    input_json_delta) still yields one inspectable segment."""
    stream = (
        b"event: content_block_start\n"
        b'data: {"type":"content_block_start","index":0,"content_block":'
        b'{"type":"tool_use","id":"t1","name":"shell","input":{"command": "ls"}}}\n\n'
        b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
    )
    c = anthropic.extract_stream_text(stream)
    assert c.tool_calls == (tool_call_segment("shell", {"command": "ls"}),)
    assert c.uninspected == ()


def test_stream_fragments_that_cannot_be_accumulated_are_gaps():
    """4.4: missing index, non-object entries, and nothing reconstructable are
    tool_call gaps, never silent drops."""
    stream = (
        # well-formed call on index 1, plus un-accumulatable fragments:
        b'data: {"choices":[{"delta":{"tool_calls":'
        b'[{"function":{"name":"bad","arguments":"{}"}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":["non-object"]}}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0}]}}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":'
        b'[{"index":1,"function":{"name":"ok","arguments":"{\\"a\\":1}"}}]}}]}\n\n'
    )
    c = openai.extract_stream_text(stream)
    assert c.tool_calls == (tool_call_segment("ok", {"a": 1}),)
    tool_call_gaps = [gap for gap in c.uninspected if gap.kind == GAP_TOOL_CALL]
    assert len(tool_call_gaps) == 3
    # the index-1 call reconstructed; the bare index-0 slot did not
    assert c.uninspected[0].location == "choices[0].delta.tool_calls"


def test_stream_tool_call_gaps_never_contain_raw_content():
    """4.4 + redaction guard: un-accumulatable fragment content stays out of
    every gap detail and location."""
    secret = "SECRET-STREAM-FRAGMENT-0123456789"
    stream = (
        b'data: {"choices":[{"delta":{"tool_calls":[{"function":{"name":"'
        + secret.encode()
        + b'","arguments":"'
        + secret.encode()
        + b'"}}]}}]}\n\n'
        + b'data: {"choices":[{"delta":{"tool_calls":["'
        + secret.encode()
        + b'"]}}]}\n\n'
        + b'data: {"choices":[{"delta":{"tool_calls":[{"index":0}]}}]}\n\n'
    )
    c = openai.extract_stream_text(stream)
    assert c.tool_calls == ()
    for gap in c.uninspected:
        assert secret not in gap.detail
        assert secret not in gap.location

    ant_stream = (
        b"event: content_block_start\n"
        + b'data: {"type":"content_block_start","index":0,"content_block":'
        + b'{"type":"tool_use","id":"t1","name":"'
        + secret.encode()
        + b'","input":{}}}\n\n'
    )
    ant = anthropic.extract_stream_text(ant_stream)
    assert ant.tool_calls == (tool_call_segment(secret, None),)  # name is inspected content
    assert all(secret not in gap.detail for gap in ant.uninspected)


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
    openai_stream = openai.extract_stream_text(stream)
    ant_stream = anthropic.extract_stream_text(stream)
    for gap in [*openai_stream.uninspected, *ant_stream.uninspected]:
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
