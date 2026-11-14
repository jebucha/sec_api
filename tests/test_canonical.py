"""Canonical dataclass construction and equality (task 4.1)."""

from __future__ import annotations

from llmgw.formats.canonical import CANONICAL_FIELDS, Canonical, Gap


def test_canonical_fields_match_the_contract():
    assert set(CANONICAL_FIELDS) == {
        "system",
        "user_text",
        "assistant_text",
        "tool_calls",
        "tool_results",
    }


def test_canonical_defaults_are_empty():
    c = Canonical()
    assert c.system == ()
    assert c.user_text == ()
    assert c.assistant_text == ()
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
