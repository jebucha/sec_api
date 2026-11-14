"""Immutable ruleset and content hash (design D8).

A ``Ruleset`` holds only the enabled (active) rules. Its hash is a
deterministic sha256 over the canonical JSON of the active rules sorted by
id, and is included in every security event for correlation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .heuristics import Span


@dataclass(frozen=True)
class Rule:
    """A validated, compiled detection rule."""

    id: str
    name: str
    version: str
    stage: str  # request | response
    target: tuple[str, ...]
    type: str  # regex | keyword | heuristic
    severity: str
    action: str  # block | flag | log
    enabled: bool
    description: str | None
    owasp: tuple[str, ...]
    atlas: tuple[str, ...]
    tags: tuple[str, ...]
    mask_excerpt: bool
    file: str
    line: int
    matcher: Callable[[str], list[Span]]
    raw: dict[str, Any]  # the validated rule object (for hashing)


def ruleset_hash(active_rules: list[Rule]) -> str:
    """Deterministic sha256 over the active rules' raw objects sorted by id."""
    payload = json.dumps(
        sorted((rule.raw for rule in active_rules), key=lambda raw: raw["id"]),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Ruleset:
    """The set of active rules, swapped atomically by reference on reload."""

    rules: tuple[Rule, ...]
    hash: str

    def rules_for_stage(self, stage: str) -> tuple[Rule, ...]:
        return tuple(rule for rule in self.rules if rule.stage == stage)
