"""Detection engine: evaluate active rules against canonical content.

Pure module: no events, no I/O. The caller (proxy) turns the returned
``Verdict`` into events and responses. Masking of sensitive excerpts happens
here, before events are built (design D12), so unmasked values never reach
the event layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .formats.canonical import Canonical
from .rules.ruleset import Ruleset

MODES = ("monitor", "enforce")
# Most severe wins: block > flag > log > allow.
ACTION_PRECEDENCE = ("allow", "log", "flag", "block")


@dataclass(frozen=True)
class Match:
    rule_id: str
    severity: str
    action: str
    owasp: tuple[str, ...]
    atlas: tuple[str, ...]
    field: str  # canonical field name
    segment_index: int
    excerpt: str  # bounded, and masked when the rule sets mask_excerpt


@dataclass(frozen=True)
class RuleError:
    rule_id: str
    error: str  # bounded; must not contain matched content


@dataclass(frozen=True)
class Verdict:
    """The resolved stage verdict.

    ``action`` is ``allow`` when nothing matched and no detection error
    forced a block. ``enforced`` is true only for an enforced block
    (enforce mode + block action).
    """

    action: str
    enforced: bool
    matches: tuple[Match, ...]
    rule_errors: tuple[RuleError, ...]

    @property
    def blocked(self) -> bool:
        return self.action == "block" and self.enforced

    @property
    def has_matches(self) -> bool:
        return bool(self.matches)


def _make_excerpt(segment: str, start: int, end: int, max_chars: int, mask: bool) -> str:
    text = segment[start:end]
    if mask:
        if len(text) < 5:
            return f"**** ({len(text)} chars)"
        return f"{text[:2]}\u2026{text[-2:]} ({len(text)} chars)"
    if len(text) > max_chars:
        return text[:max_chars]
    return text


def _worst_action(actions: list[str]) -> str:
    worst = "allow"
    for action in actions:
        if ACTION_PRECEDENCE.index(action) > ACTION_PRECEDENCE.index(worst):
            worst = action
    return worst


def evaluate(
    ruleset: Ruleset,
    canonical: Canonical,
    *,
    stage: str,
    mode: str,
    excerpt_max_chars: int = 200,
) -> Verdict:
    """Evaluate all active rules for ``stage`` against the canonical structure.

    - Only the ruleset's active (enabled) rules are evaluated.
    - A rule only sees the canonical fields in its ``target``.
    - All matching rules are recorded (no short-circuit).
    - The verdict is the most severe matching action
      (``block > flag > log > allow``).
    - A rule that raises is recorded as a ``RuleError``: in enforce mode the
      stage is treated as blocked; in monitor mode traffic proceeds.
    """
    matches: list[Match] = []
    errors: list[RuleError] = []

    for rule in ruleset.rules_for_stage(stage):
        try:
            for field in rule.target:
                segments = getattr(canonical, field)
                for segment_index, segment in enumerate(segments):
                    for span in rule.matcher(segment):
                        matches.append(
                            Match(
                                rule_id=rule.id,
                                severity=rule.severity,
                                action=rule.action,
                                owasp=rule.owasp,
                                atlas=rule.atlas,
                                field=field,
                                segment_index=segment_index,
                                excerpt=_make_excerpt(
                                    segment,
                                    span.start,
                                    span.end,
                                    excerpt_max_chars,
                                    rule.mask_excerpt,
                                ),
                            )
                        )
        except Exception as exc:  # detection errors fail per mode
            errors.append(
                RuleError(rule_id=rule.id, error=f"{type(exc).__name__}: {exc}"[:200])
            )

    base_action = _worst_action([match.action for match in matches])
    if errors and mode == "enforce":
        action = "block"
    else:
        action = base_action
    enforced = mode == "enforce" and action == "block"

    return Verdict(
        action=action, enforced=enforced, matches=tuple(matches), rule_errors=tuple(errors)
    )
