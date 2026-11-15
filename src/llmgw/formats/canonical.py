"""Canonical inspection structure (design D5).

The canonical form is inspection-only: the original bytes are always what is
forwarded upstream. Fields are lists of segments (one segment per message
part), not concatenated strings, so match excerpts refer to a single message
and a pattern cannot match across a message boundary.

``tool_calls`` carries one JSON segment per tool call (see
:func:`tool_call_segment`); ``tool_results`` carries one segment per tool
result text. The format parsers populate both at the request and response
stages, and they are inspected like any other canonical field. The gap kinds
that remain uninspected are: tool definitions (``tools[]``/``functions``),
images, unknown part types, legacy ``function_call`` fields, malformed tool
content (non-list ``tool_calls``, non-object call entries, unparseable
stream fragments), and stream issues.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

CANONICAL_FIELDS = ("system", "user_text", "assistant_text", "tool_calls", "tool_results")

# Gap kinds for uninspected content
GAP_TOOL_CALL = "tool_call"
GAP_TOOL_RESULT = "tool_result"
GAP_TOOL_DEFINITIONS = "tool_definitions"
GAP_IMAGE = "image"
GAP_UNKNOWN_PART = "unknown_part"
GAP_STREAM_ERROR = "stream_error"
GAP_STREAM_OVERFLOW = "stream_overflow"
GAP_OTHER = "other"


def tool_call_segment(name: str, arguments: object) -> str:
    """One canonical tool-call segment (design D1).

    ``arguments`` is OpenAI's ``function.arguments`` string or Anthropic's
    ``tool_use.input`` object. A string is parsed leniently to the input
    object so both formats produce the same segment; a string that does not
    parse is kept verbatim as ``input``. Serialization is deterministic
    (``sort_keys=True``), so a single pattern sees name and arguments
    together in a fixed encoding.
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            pass
    return json.dumps({"input": arguments, "name": name}, sort_keys=True)


@dataclass(frozen=True)
class Gap:
    """A piece of content the parser does not inspect in this version."""

    kind: str  # one of the GAP_* constants
    location: str  # human-readable location, e.g. "messages[2].content[1]"
    detail: str = ""  # short description; must never contain raw content


@dataclass(frozen=True)
class Canonical:
    system: tuple[str, ...] = ()
    user_text: tuple[str, ...] = ()
    assistant_text: tuple[str, ...] = ()
    tool_calls: tuple = ()
    tool_results: tuple = ()
    uninspected: tuple[Gap, ...] = field(default_factory=tuple)

    def segment_lists(self) -> dict[str, tuple[str, ...]]:
        """The text-bearing canonical fields, keyed by field name."""
        return {
            "system": self.system,
            "user_text": self.user_text,
            "assistant_text": self.assistant_text,
        }
