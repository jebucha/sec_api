"""Labeled corpus loading and validation (detection-testing: Corpus format).

A corpus file is JSONL; each line is one labeled example:

    {"id", "text", "label": "malicious"|"benign", "category",
     optional "expected_rules": [rule ids], "field" (default "user_text"),
     "stage" (default "request"), "format" (default "openai")}

Blank lines and lines beginning with ``//`` are ignored. Invalid lines are
reported (file, line, reason) and excluded from metrics; they never abort
the run (detection-testing: Invalid corpus line).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

VALID_LABELS = ("malicious", "benign")
VALID_STAGES = ("request", "response")
VALID_FORMATS = ("openai", "anthropic")

# Canonical fields that carry text segments in v1.
TEXT_FIELDS = ("system", "user_text", "assistant_text", "reasoning_text")

REQUIRED_FIELDS = ("id", "text", "label", "category")
OPTIONAL_FIELDS = ("expected_rules", "field", "stage", "format")


@dataclass(frozen=True)
class CorpusEntry:
    """One validated corpus line."""

    id: str
    text: str
    label: str  # malicious | benign
    category: str
    expected_rules: tuple[str, ...] = ()
    field: str = "user_text"
    stage: str = "request"
    format: str = "openai"


@dataclass(frozen=True)
class InvalidLine:
    """A corpus line that failed validation, with the reason to report."""

    file: str
    line: int
    reason: str


def _parse_line(path: Path, line_no: int, stripped: str) -> CorpusEntry | InvalidLine:
    file = str(path)
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return InvalidLine(file, line_no, f"invalid JSON: {exc}")
    if not isinstance(obj, dict):
        return InvalidLine(file, line_no, "corpus line must be a JSON object")
    unknown = set(obj) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS)
    if unknown:
        return InvalidLine(file, line_no, f"unknown fields: {sorted(unknown)}")
    for name in REQUIRED_FIELDS:
        value = obj.get(name)
        if not isinstance(value, str) or not value:
            return InvalidLine(file, line_no, f"missing or invalid required field: {name!r}")
    if obj["label"] not in VALID_LABELS:
        return InvalidLine(file, line_no, f"label must be one of {list(VALID_LABELS)}")
    expected_rules = obj.get("expected_rules")
    if expected_rules is None:
        expected_rules = []
    if not isinstance(expected_rules, list) or not all(
        isinstance(rule, str) and rule for rule in expected_rules
    ):
        return InvalidLine(file, line_no, "expected_rules must be a list of non-empty strings")
    entry_field = obj.get("field", "user_text")
    if entry_field not in TEXT_FIELDS:
        return InvalidLine(file, line_no, f"field must be one of {list(TEXT_FIELDS)}")
    stage = obj.get("stage", "request")
    if stage not in VALID_STAGES:
        return InvalidLine(file, line_no, f"stage must be one of {list(VALID_STAGES)}")
    fmt = obj.get("format", "openai")
    if fmt not in VALID_FORMATS:
        return InvalidLine(file, line_no, f"format must be one of {list(VALID_FORMATS)}")
    return CorpusEntry(
        id=obj["id"],
        text=obj["text"],
        label=obj["label"],
        category=obj["category"],
        expected_rules=tuple(expected_rules),
        field=entry_field,
        stage=stage,
        format=fmt,
    )


def _expand(paths: str | Path | list[str | Path]) -> list[Path]:
    files: list[Path] = []
    items: list[str | Path] = [paths] if isinstance(paths, (str, Path)) else list(paths)
    for item in items:
        path = Path(item)
        if path.is_dir():
            files.extend(sorted(p for p in path.glob("*.jsonl") if p.is_file()))
        else:
            files.append(path)
    return files


def load_corpus(
    paths: str | Path | list[str | Path],
) -> tuple[list[CorpusEntry], list[InvalidLine]]:
    """Load one or more corpus files (or directories of ``*.jsonl``).

    Returns (entries, invalid): validated entries in file order, and the
    invalid lines with their reasons.
    """
    entries: list[CorpusEntry] = []
    invalid: list[InvalidLine] = []
    seen_ids: set[str] = set()
    for path in _expand(paths):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            invalid.append(InvalidLine(str(path), 0, f"cannot read: {exc}"))
            continue
        for line_no, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            parsed = _parse_line(path, line_no, stripped)
            if isinstance(parsed, InvalidLine):
                invalid.append(parsed)
                continue
            if parsed.id in seen_ids:
                reason = f"duplicate corpus id {parsed.id!r}"
                invalid.append(InvalidLine(str(path), line_no, reason))
                continue
            seen_ids.add(parsed.id)
            entries.append(parsed)
    return entries, invalid
