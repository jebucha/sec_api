"""Built-in heuristics registry (design D7).

Heuristics are code, not rule files: a ``heuristic`` rule names one of these
plus optional ``params``. Each heuristic is a pure function over one canonical
segment that returns the spans it matched. The registry is fixed by the
gateway version, so rule packs cannot introduce executable logic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import re2


@dataclass(frozen=True)
class Span:
    """A half-open [start, end) match offset within one segment."""

    start: int
    end: int


# A segment is one text segment; params are the validated, default-filled
# params object. Returns the spans matched.
HeuristicFunc = Callable[[str, dict[str, Any]], list[Span]]


@dataclass(frozen=True)
class HeuristicSpec:
    name: str
    description: str
    func: HeuristicFunc
    # param name -> (type, default). Unknown params are rejected at load time.
    params_spec: dict[str, tuple[type, Any]]


def _require_params(params: dict[str, Any], spec: dict[str, tuple[type, Any]]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for name, (ptype, default) in spec.items():
        value = params.get(name, default)
        if not isinstance(value, ptype) or (ptype is int and isinstance(value, bool)):
            raise ValueError(f"param {name!r} must be of type {ptype.__name__}")
        resolved[name] = value
    unknown = set(params) - set(spec)
    if unknown:
        raise ValueError(f"unknown params: {sorted(unknown)}")
    return resolved


# --- luhn_card -----------------------------------------------------------------


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


_CANDIDATE_CARD = re2.compile(r"\b\d(?:[ -]?\d){12,18}\b")


def _luhn_card(segment: str, params: dict[str, Any]) -> list[Span]:
    min_digits = params["min_digits"]
    max_digits = params["max_digits"]
    spans: list[Span] = []
    for m in _CANDIDATE_CARD.finditer(segment):
        digits = re2.sub(r"[ -]", "", m.group(0))
        if min_digits <= len(digits) <= max_digits and _luhn_valid(digits):
            spans.append(Span(m.start(), m.end()))
    return spans


# --- unicode_tags ----------------------------------------------------------------


def _unicode_tags(segment: str, params: dict[str, Any]) -> list[Span]:
    # Tag block U+E0000..U+E007F; RE2 uses Go-style \x{...} escapes.
    pattern = re2.compile(f"[\\x{{E0000}}-\\x{{E007F}}]{{{params['min_run']},}}")
    return [Span(m.start(), m.end()) for m in pattern.finditer(segment)]


# --- zero_width ------------------------------------------------------------------

# RE2 uses Go-style \x{...} escapes, not \u.
_ZERO_WIDTH_CLASS = r"[\x{200b}-\x{200f}\x{202a}-\x{202e}\x{2060}-\x{2064}\x{feff}]"


def _zero_width(segment: str, params: dict[str, Any]) -> list[Span]:
    pattern = re2.compile(_ZERO_WIDTH_CLASS + f"{{{params['min_run']},}}")
    return [Span(m.start(), m.end()) for m in pattern.finditer(segment)]


# --- base64_run ------------------------------------------------------------------


def _base64_run(segment: str, params: dict[str, Any]) -> list[Span]:
    min_length = params["min_length"]
    pattern = re2.compile(f"[A-Za-z0-9+/]{{{min_length},}}={{0,2}}")
    spans: list[Span] = []
    for m in pattern.finditer(segment):
        text = m.group(0)
        # The trailing ={0,2} is optional padding; keep the alphanumeric core.
        core = re2.match(r"[A-Za-z0-9+/]+", text).group(0)
        if len(core) >= min_length:
            spans.append(Span(m.start(), m.start() + len(core)))
    return spans


# --- canary ------------------------------------------------------------------------


def _canary(segment: str, params: dict[str, Any]) -> list[Span]:
    canaries: list[str] = params.get("canaries") or []
    if not canaries:
        return []
    pattern = re2.compile("|".join(re2.escape(c) for c in sorted(canaries, key=len, reverse=True)))
    return [Span(m.start(), m.end()) for m in pattern.finditer(segment)]


REGISTRY: dict[str, HeuristicSpec] = {
    spec.name: spec
    for spec in [
        HeuristicSpec(
            name="luhn_card",
            description="Credit-card-shaped digit runs (13-19 digits, optional space/dash "
            "separators) that pass the Luhn checksum. Random digit runs are rejected.",
            func=_luhn_card,
            params_spec={
                "min_digits": (int, 13),
                "max_digits": (int, 19),
            },
        ),
        HeuristicSpec(
            name="unicode_tags",
            description="Runs of Unicode tag characters (U+E0000..U+E007F), used to smuggle "
            "invisible payloads.",
            func=_unicode_tags,
            params_spec={"min_run": (int, 2)},
        ),
        HeuristicSpec(
            name="zero_width",
            description="Runs of zero-width / directional characters (U+200B..200F, "
            "U+202A..202E, U+2060..2064, U+FEFF).",
            func=_zero_width,
            params_spec={"min_run": (int, 1)},
        ),
        HeuristicSpec(
            name="base64_run",
            description="Long runs of base64 alphabet characters (possible encoding smuggling).",
            func=_base64_run,
            params_spec={"min_length": (int, 40)},
        ),
        HeuristicSpec(
            name="canary",
            description="Any configured canary token (prompt-leak detection). Canary values "
            "come from the environment (config canary_tokens_env) or from the replay "
            "tool's --canaries option; they never live in the rule pack.",
            func=_canary,
            params_spec={"canaries": (list, [])},
        ),
    ]
}


class UnknownHeuristicError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"unknown heuristic: {name!r}")
        self.name = name


def validate_and_fill_params(name: str, params: dict[str, Any] | None) -> dict[str, Any]:
    """Validate a heuristic's params against its spec, filling defaults.

    Raises ValueError for unknown or mistyped params.
    """
    spec = REGISTRY.get(name)
    if spec is None:
        raise UnknownHeuristicError(name)
    return _require_params(params or {}, spec.params_spec)


def make_matcher(name: str, params: dict[str, Any]) -> Callable[[str], list[Span]]:
    """Compile a heuristic into a matcher closure over segments."""
    spec = REGISTRY.get(name)
    if spec is None:
        raise UnknownHeuristicError(name)

    def matcher(segment: str) -> list[Span]:
        return spec.func(segment, params)

    return matcher
