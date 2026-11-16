"""Rule pack loading: JSONL -> validate -> compile -> self-test -> Ruleset.

The published schema lives at ``rules/schema.json`` (repo); the loader uses an
embedded copy (``RULE_SCHEMA``) so it works wherever the package is installed.
A test asserts the two stay in sync.

Every rejection emits a ``rule_rejected`` event (file, line, id if parseable,
reason) through the optional ``event_sink`` callback.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import re2

from ..events import rule_rejected_event
from .heuristics import REGISTRY, UnknownHeuristicError, make_matcher, validate_and_fill_params
from .ruleset import Rule, Ruleset, ruleset_hash

# Must stay equal to rules/schema.json (checked by tests).
RULE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "llmgw detection rule",
    "type": "object",
    "required": [
        "id",
        "name",
        "version",
        "stage",
        "target",
        "type",
        "severity",
        "action",
        "enabled",
    ],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]*$"},
        "name": {"type": "string", "minLength": 1},
        "version": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "stage": {"enum": ["request", "response"]},
        "target": {
            "type": "array",
            "minItems": 1,
            "items": {
                "enum": [
                    "system",
                    "user_text",
                    "assistant_text",
                    "reasoning_text",
                    "tool_calls",
                    "tool_results",
                    "tool_definitions",
                ]
            },
        },
        "type": {"enum": ["regex", "keyword", "heuristic"]},
        "severity": {"enum": ["low", "medium", "high", "critical"]},
        "action": {"enum": ["block", "flag", "log"]},
        "enabled": {"type": "boolean"},
        "owasp": {"type": "array", "items": {"type": "string"}},
        "atlas": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "mask_excerpt": {"type": "boolean"},
        "pattern": {"type": "string", "minLength": 1},
        "keywords": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
        "case_sensitive": {"type": "boolean", "default": False},
        "heuristic": {"type": "string", "minLength": 1},
        "params": {"type": "object"},
        "tests": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "match": {"type": "array", "items": {"type": "string"}},
                "no_match": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "allOf": [
        {
            "if": {"properties": {"type": {"const": "regex"}}, "required": ["type"]},
            "then": {"required": ["pattern"]},
        },
        {
            "if": {"properties": {"type": {"const": "keyword"}}, "required": ["type"]},
            "then": {"required": ["keywords"]},
        },
        {
            "if": {"properties": {"type": {"const": "heuristic"}}, "required": ["type"]},
            "then": {"required": ["heuristic"]},
        },
    ],
}


class RulesetLoadError(Exception):
    """The rules directory itself cannot be read; loading cannot proceed."""


@dataclass(frozen=True)
class LoadResult:
    ruleset: Ruleset
    accepted: int
    rejected: int


EventSink = Callable[[dict[str, Any]], None]


def _reject(sink: EventSink | None, file: str, line: int, rule_id: str | None, reason: str) -> None:
    if sink is not None:
        sink(rule_rejected_event(file=file, line=line, rule_id=rule_id, reason=reason))


def _compile(rule: dict[str, Any], canary_values: list[str]):
    """Build the matcher for a schema-valid rule. Raises on failure."""
    rtype = rule["type"]
    if rtype == "regex":
        # re2 raises on patterns it cannot compile (lookarounds, backrefs).
        pattern = re2.compile(rule["pattern"])
        return lambda segment: [
            (m.start(), m.end()) for m in pattern.finditer(segment)
        ]

    if rtype == "keyword":
        options = re2.Options()
        options.case_sensitive = bool(rule.get("case_sensitive", False))
        alternation = "|".join(re.escape(k) for k in rule["keywords"])
        pattern = re2.compile(f"(?:{alternation})", options)
        return lambda segment: [(m.start(), m.end()) for m in pattern.finditer(segment)]

    if rtype == "heuristic":
        name = rule["heuristic"]
        if name not in REGISTRY:
            raise UnknownHeuristicError(name)
        params = validate_and_fill_params(name, rule.get("params") or {})
        if name == "canary":
            # Canary values never come from the rule pack (design D11):
            # they are injected from the environment or the replay tool.
            params = dict(params, canaries=canary_values)
        heuristic_matcher = make_matcher(name, params)
        return lambda segment: [(s.start, s.end) for s in heuristic_matcher(segment)]

    raise ValueError(f"unknown rule type: {rtype!r}")


def _run_self_tests(rule: dict[str, Any], matcher) -> str | None:
    """Return a rejection reason if an inline self-test fails, else None."""
    tests = rule.get("tests") or {}
    for index, example in enumerate(tests.get("match") or []):
        if not matcher(example):
            return f"self-test tests.match[{index}] failed to match"
    for index, example in enumerate(tests.get("no_match") or []):
        if matcher(example):
            return f"self-test tests.no_match[{index}] unexpectedly matched"
    return None


def _load_file(
    path: Path, sink: EventSink | None
) -> tuple[list[tuple[Path, int, dict[str, Any]]], list[tuple[Path, int, str]], int]:
    """Parse and schema-validate one file.

    Returns (candidates, id_entries, rejected_count). ``candidates`` are
    (file, line, rule-object) tuples that passed schema validation;
    ``id_entries`` records every parsed object with a string id, including
    schema-invalid ones, so duplicate ids are still detected across files.
    A file that cannot be read is rejected (not fatal) via the event sink.
    """
    candidates: list[tuple[Path, int, dict[str, Any]]] = []
    id_entries: list[tuple[Path, int, str]] = []
    rejected = 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _reject(sink, str(path), 0, None, f"cannot read file: {exc.strerror or exc}")
        return [], [], 1
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            _reject(sink, str(path), line_no, None, f"invalid JSON: {exc}")
            rejected += 1
            continue
        if not isinstance(obj, dict):
            _reject(sink, str(path), line_no, None, "rule must be a JSON object")
            rejected += 1
            continue
        rule_id = obj.get("id")
        if isinstance(rule_id, str):
            id_entries.append((path, line_no, rule_id))
        try:
            jsonschema.validate(obj, RULE_SCHEMA)
        except jsonschema.ValidationError as exc:
            _reject(
                sink,
                str(path),
                line_no,
                rule_id if isinstance(rule_id, str) else None,
                f"schema violation: {exc.message}",
            )
            rejected += 1
            continue
        candidates.append((path, line_no, obj))
    return candidates, id_entries, rejected


def load_rules(
    rules_dir: str | Path,
    *,
    event_sink: EventSink | None = None,
    canary_values: list[str] | None = None,
) -> LoadResult:
    """Load all ``*.jsonl`` packs in ``rules_dir`` into a Ruleset.

    Rejections never abort the load: invalid rules are rejected (with
    ``rule_rejected`` events) and the rest load. Raises RulesetLoadError only
    when the directory itself cannot be read.
    """
    directory = Path(rules_dir)
    if not directory.is_dir():
        raise RulesetLoadError(f"rules directory cannot be read: {directory}")
    try:
        files = sorted(p for p in directory.iterdir() if p.suffix == ".jsonl" and p.is_file())
    except OSError as exc:
        raise RulesetLoadError(f"rules directory cannot be read: {directory} ({exc})") from exc

    canaries = list(canary_values or [])
    candidates: list[tuple[Path, int, dict[str, Any]]] = []
    id_entries: list[tuple[Path, int, str]] = []
    rejected = 0
    for path in files:
        file_candidates, file_ids, file_rejected = _load_file(path, event_sink)
        candidates.extend(file_candidates)
        id_entries.extend(file_ids)
        rejected += file_rejected

    # Duplicate ids: every occurrence is rejected, including a schema-invalid
    # line whose valid twin would otherwise still load.
    id_counts: dict[str, int] = {}
    for _path, _line, rule_id in id_entries:
        id_counts[rule_id] = id_counts.get(rule_id, 0) + 1
    duplicates = {rule_id for rule_id, count in id_counts.items() if count > 1}

    rules: list[Rule] = []
    for path, line_no, obj in candidates:
        rule_id = obj["id"]
        if rule_id in duplicates:
            _reject(event_sink, str(path), line_no, rule_id, f"duplicate id {rule_id!r}")
            rejected += 1
            continue
        try:
            matcher = _compile(obj, canaries)
        except UnknownHeuristicError as exc:
            _reject(event_sink, str(path), line_no, rule_id, str(exc))
            rejected += 1
            continue
        except (re2.error, ValueError) as exc:
            _reject(event_sink, str(path), line_no, rule_id, f"compile failed: {exc}")
            rejected += 1
            continue
        self_test_failure = _run_self_tests(obj, matcher)
        if self_test_failure is not None:
            _reject(event_sink, str(path), line_no, rule_id, self_test_failure)
            rejected += 1
            continue
        from .heuristics import Span

        def matcher_fn(segment, _m=matcher):
            return [Span(*p) for p in _m(segment)]

        rules.append(
            Rule(
                id=rule_id,
                name=obj["name"],
                version=obj["version"],
                stage=obj["stage"],
                target=tuple(obj["target"]),
                type=obj["type"],
                severity=obj["severity"],
                action=obj["action"],
                enabled=bool(obj["enabled"]),
                description=obj.get("description"),
                owasp=tuple(obj.get("owasp") or ()),
                atlas=tuple(obj.get("atlas") or ()),
                tags=tuple(obj.get("tags") or ()),
                mask_excerpt=bool(obj.get("mask_excerpt", False)),
                file=str(path),
                line=line_no,
                matcher=matcher_fn,
                raw=obj,
            )
        )

    active = [rule for rule in rules if rule.enabled]
    ruleset = Ruleset(rules=tuple(active), hash=ruleset_hash(active))
    return LoadResult(ruleset=ruleset, accepted=len(rules), rejected=rejected)
