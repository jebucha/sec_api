"""Canonical dataclass construction and equality (task 4.1)."""

from __future__ import annotations

import json

from llmgw.formats.canonical import (
    CANONICAL_FIELDS,
    Canonical,
    Gap,
    tool_definition_segment,
)


def test_canonical_fields_match_the_contract():
    assert set(CANONICAL_FIELDS) == {
        "system",
        "user_text",
        "assistant_text",
        "reasoning_text",
        "tool_calls",
        "tool_results",
        "tool_definitions",
    }


def test_canonical_defaults_are_empty():
    c = Canonical()
    assert c.system == ()
    assert c.user_text == ()
    assert c.assistant_text == ()
    assert c.reasoning_text == ()
    assert c.tool_calls == ()
    assert c.tool_results == ()
    assert c.uninspected == ()


def test_canonical_construction_and_equality():
    a = Canonical(
        system=("s1",),
        user_text=("u1", "u2"),
        assistant_text=("a1",),
        uninspected=(Gap(kind="image", location="messages[0].content[0]"),),
    )
    b = Canonical(
        system=("s1",),
        user_text=("u1", "u2"),
        assistant_text=("a1",),
        uninspected=(Gap(kind="image", location="messages[0].content[0]"),),
    )
    c = Canonical(system=("s1",), user_text=("u1",))
    assert a == b
    assert a != c
    # segment lists preserve per-message boundaries
    assert "u1" in a.segment_lists()["user_text"]


def test_canonical_is_immutable():
    c = Canonical(system=("s",))
    try:
        c.system = ("other",)  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("Canonical must be frozen")


# --- tool-definition segment builder (design D3) ------------------------------


def test_tool_definition_segment_serialization_is_deterministic():
    """One segment per tool, sorted keys, regardless of input key order."""
    schema_a = {"type": "object", "properties": {"command": {"type": "string"}}}
    schema_b = {"properties": {"command": {"type": "string"}}, "type": "object"}
    a = tool_definition_segment("shell", "Run a command", schema_a)
    b = tool_definition_segment("shell", "Run a command", schema_b)
    assert a == b
    assert a == json.dumps(
        {
            "description": "Run a command",
            "input_schema": {"properties": {"command": {"type": "string"}}, "type": "object"},
            "name": "shell",
        },
        sort_keys=True,
    )


def test_tool_definition_segment_missing_fields_serialize_null():
    """A missing description or schema becomes ``null`` in the segment."""
    segment = tool_definition_segment("shell", None, None)
    assert json.loads(segment) == {"description": None, "input_schema": None, "name": "shell"}
    assert json.loads(segment)["description"] is None


def test_tool_definition_segment_is_format_independent():
    """The same tool through either wire format normalizes to the same
    builder arguments and yields byte-identical segment bytes: the JSON
    Schema's own key order (wire noise) must not leak into the segment."""
    oai_schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }
    ant_schema = {
        "required": ["command"],
        "properties": {"command": {"type": "string"}},
        "type": "object",
    }
    from_openai = tool_definition_segment("shell", "Run a command", oai_schema)
    from_anthropic = tool_definition_segment("shell", "Run a command", ant_schema)
    assert from_openai == from_anthropic
