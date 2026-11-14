"""Canonical inspection structure (design D5).

The canonical form is inspection-only: the original bytes are always what is
forwarded upstream. Fields are lists of segments (one segment per message
part), not concatenated strings, so match excerpts refer to a single message
and a pattern cannot match across a message boundary.

``tool_calls``/``tool_results`` exist as the seam for the deferred
tool-inspection change and stay empty in v1; their content is recorded in
``uninspected`` instead.
"""

from __future__ import annotations

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
