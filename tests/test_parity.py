"""Format-parity suite: the same payload through both formats.

This is the translation-bypass check: semantically identical requests must
yield identical canonical text regardless of wire format.
"""

from __future__ import annotations

import json

from llmgw.formats import anthropic, openai


def test_identical_requests_yield_identical_canonical_fields():
    """T-CONTENT-NORMALIZATION-001"""
    oai_body = json.dumps(
        {
            "model": "qwen3-32b",
            "messages": [
                {"role": "system", "content": "You are a helpful lab assistant."},
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": "4."},
                {"role": "user", "content": [{"type": "text", "text": "Explain briefly."}]},
            ],
        }
    ).encode()
    ant_body = json.dumps(
        {
            "model": "qwen3-32b",
            "system": [{"type": "text", "text": "You are a helpful lab assistant."}],
            "messages": [
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": [{"type": "text", "text": "4."}]},
                {"role": "user", "content": [{"type": "text", "text": "Explain briefly."}]},
            ],
        }
    ).encode()

    oai_canon = openai.parse_request(oai_body)
    ant_canon = anthropic.parse_request(ant_body)

    assert oai_canon.system == ant_canon.system
    assert oai_canon.user_text == ant_canon.user_text
    assert oai_canon.assistant_text == ant_canon.assistant_text
    # sanity: the shared values are the expected ones
    assert oai_canon.system == ("You are a helpful lab assistant.",)
    assert oai_canon.user_text == ("What is 2+2?", "Explain briefly.")
    assert oai_canon.assistant_text == ("4.",)


def test_identical_responses_yield_identical_canonical_fields():
    oai_body = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": "The answer is 4."}}]}
    ).encode()
    ant_body = json.dumps({"content": [{"type": "text", "text": "The answer is 4."}]}).encode()

    oai_canon = openai.parse_response(oai_body)
    ant_canon = anthropic.parse_response(ant_body)
    assert oai_canon.assistant_text == ant_canon.assistant_text == ("The answer is 4.",)


def test_identical_reasoning_content_yields_identical_canonical_field():
    """T-CONTENT-NORMALIZATION-029: one unit of reasoning, both formats."""
    oai_body = json.dumps(
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
    ant_body = json.dumps(
        {
            "content": [
                {"type": "thinking", "thinking": "I should add 2 and 2."},
                {"type": "text", "text": "The answer is 4."},
            ]
        }
    ).encode()

    oai_canon = openai.parse_response(oai_body)
    ant_canon = anthropic.parse_response(ant_body)
    assert oai_canon.reasoning_text == ant_canon.reasoning_text
    assert oai_canon.reasoning_text == ("I should add 2 and 2.",)
    assert oai_canon.assistant_text == ant_canon.assistant_text == ("The answer is 4.",)


def test_identical_tool_content_yields_identical_canonical_fields():
    """T-CONTENT-NORMALIZATION-024: one tool call + one tool result, both formats."""
    oai_body = json.dumps(
        {
            "model": "qwen3-32b",
            "messages": [
                {"role": "user", "content": "list the files"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "shell",
                                "arguments": '{"command": "ls -la", "cwd": "/tmp"}',
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "total 0\nfile.txt"},
            ],
        }
    ).encode()
    ant_body = json.dumps(
        {
            "model": "llama-3.1-8b",
            "messages": [
                {"role": "user", "content": "list the files"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "shell",
                            "input": {"command": "ls -la", "cwd": "/tmp"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "total 0\nfile.txt"}
                    ],
                },
            ],
        }
    ).encode()

    oai_canon = openai.parse_request(oai_body)
    ant_canon = anthropic.parse_request(ant_body)

    assert oai_canon.tool_calls == ant_canon.tool_calls
    assert oai_canon.tool_results == ant_canon.tool_results
    # sanity: the shared D1 segment and the shared result text
    expected_segment = json.dumps(
        {"input": {"command": "ls -la", "cwd": "/tmp"}, "name": "shell"}, sort_keys=True
    )
    assert oai_canon.tool_calls == (expected_segment,)
    assert oai_canon.tool_results == ("total 0\nfile.txt",)


def test_identical_tool_definitions_yield_identical_canonical_field():
    """T-CONTENT-NORMALIZATION-040: semantically identical tool definitions
    (same names, descriptions, JSON-Schema shapes) through both formats yield
    identical ``tool_definitions`` segments."""
    schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }
    oai_body = json.dumps(
        {
            "model": "qwen3-32b",
            "messages": [{"role": "user", "content": "list the files"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "shell",
                        "description": "Run a command",
                        "parameters": schema,
                    },
                },
                {"type": "function", "function": {"name": "read"}},
            ],
        }
    ).encode()
    ant_body = json.dumps(
        {
            "model": "llama-3.1-8b",
            "messages": [{"role": "user", "content": "list the files"}],
            "tools": [
                {
                    "name": "shell",
                    "description": "Run a command",
                    "input_schema": schema,
                },
                {"name": "read"},
            ],
        }
    ).encode()

    oai_canon = openai.parse_request(oai_body)
    ant_canon = anthropic.parse_request(ant_body)

    assert oai_canon.tool_definitions == ant_canon.tool_definitions
    # sanity: the shared D3 segments, byte-identical across formats
    assert oai_canon.tool_definitions == (
        json.dumps(
            {
                "description": "Run a command",
                "input_schema": schema,
                "name": "shell",
            },
            sort_keys=True,
        ),
        json.dumps(
            {"description": None, "input_schema": None, "name": "read"}, sort_keys=True
        ),
    )


def test_parity_across_multipart_and_block_text():
    oai_body = json.dumps(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "part one"},
                        {"type": "text", "text": "part two"},
                    ],
                }
            ]
        }
    ).encode()
    ant_body = json.dumps(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "part one"},
                        {"type": "text", "text": "part two"},
                    ],
                }
            ]
        }
    ).encode()

    assert openai.parse_request(oai_body).user_text == anthropic.parse_request(ant_body).user_text
