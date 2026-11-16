"""Security event envelope and JSONL writer.

Every security-relevant decision is recorded as one JSON object per line.
Event fields are defined per capability spec (security-events).
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ulid import ULID

ENVELOPE_FIELDS = (
    "event_id",
    "timestamp",
    "event_type",
    "request_id",
    "client",
    "mode",
    "ruleset_hash",
)


def new_ulid() -> str:
    """A fresh time-sortable unique id."""
    return str(ULID())


def rfc3339_now() -> str:
    """The current UTC time as an RFC 3339 string (millisecond precision)."""
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def make_envelope(
    event_type: str,
    *,
    request_id: str | None = None,
    client: str | None = None,
    mode: str | None = None,
    ruleset_hash: str | None = None,
    event_id: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build the common event envelope. Fields that do not apply are null."""
    return {
        "event_id": event_id or new_ulid(),
        "timestamp": timestamp or rfc3339_now(),
        "event_type": event_type,
        "request_id": request_id,
        "client": client,
        "mode": mode,
        "ruleset_hash": ruleset_hash,
    }


@dataclass
class RequestContext:
    """Per-request identity, captured once at request arrival (design D12).

    ``request_id`` is generated before auth and shared by every event for the
    request. ``mode`` and ``ruleset_hash`` are the values captured at start so
    a hot reload mid-request does not change the events in flight.
    """

    request_id: str
    client: str | None = None
    mode: str | None = None
    ruleset_hash: str | None = None

    def envelope(self, event_type: str) -> dict[str, Any]:
        return make_envelope(
            event_type,
            request_id=self.request_id,
            client=self.client,
            mode=self.mode,
            ruleset_hash=self.ruleset_hash,
        )


def rule_rejected_event(
    *,
    file: str,
    line: int,
    rule_id: str | None,
    reason: str,
) -> dict[str, Any]:
    """A ``rule_rejected`` event (security-events: Lifecycle and gap events)."""
    event = make_envelope("rule_rejected")
    event.update({"file": file, "line": line, "id": rule_id, "reason": reason})
    return event


def verdict_event(
    ctx: RequestContext,
    *,
    stage: str,
    format: str,
    model: str | None,
    upstream: str | None,
    action: str,
    enforced: bool,
    matches: Any,
) -> dict[str, Any]:
    """A ``verdict`` event for a stage whose verdict is not ``allow``.

    ``matches`` are engine Match objects (duck-typed) or pre-shaped dicts.
    """
    event = ctx.envelope("verdict")
    event.update(
        {
            "stage": stage,
            "format": format,
            "model": model,
            "upstream": upstream,
            "action": action,
            "enforced": enforced,
            "matches": [
                _match_to_dict(match) if not isinstance(match, dict) else match
                for match in matches
            ],
        }
    )
    return event


def _match_to_dict(match: Any) -> dict[str, Any]:
    return {
        "rule_id": match.rule_id,
        "severity": match.severity,
        "owasp": list(match.owasp),
        "atlas": list(match.atlas),
        "field": match.field,
        "excerpt": match.excerpt,
    }


def rule_error_event(ctx: RequestContext, *, rule_id: str, error: str) -> dict[str, Any]:
    event = ctx.envelope("rule_error")
    event.update({"rule_id": rule_id, "error": error})
    return event


def uninspected_content_event(
    ctx: RequestContext,
    *,
    kind: str,
    location: str,
    detail: str = "",
    stage: str | None = None,
    count: int = 1,
) -> dict[str, Any]:
    """An ``uninspected_content`` event for a gap in the canonical structure.

    Duplicate gaps (same kind, location, and detail) are aggregated per stage;
    ``count`` is the number of gaps represented by this event.
    """
    event = ctx.envelope("uninspected_content")
    event.update(
        {"kind": kind, "location": location, "detail": detail, "stage": stage, "count": count}
    )
    return event


def auth_failed_event(
    ctx: RequestContext, *, source: str | None, style: str | None
) -> dict[str, Any]:
    """An ``auth_failed`` event. Carries source address and header style, never the token."""
    event = ctx.envelope("auth_failed")
    event.update({"source": source, "style": style})
    return event


def request_completed_event(
    ctx: RequestContext, *, outcome: str, status: int, latency_ms: float
) -> dict[str, Any]:
    """One per API request reaching the auth check.

    outcome: allowed | blocked | upstream_error | auth_failed
    """
    event = ctx.envelope("request_completed")
    event.update(
        {"outcome": outcome, "status": status, "latency_ms": round(latency_ms, 3)}
    )
    return event


def ruleset_loaded_event(*, ruleset_hash: str, accepted: int, rejected: int) -> dict[str, Any]:
    event = make_envelope("ruleset_loaded")
    event.update({"ruleset_hash": ruleset_hash, "accepted": accepted, "rejected": rejected})
    return event


def ruleset_reload_failed_event(
    *, reason: str, active_ruleset_hash: str | None = None
) -> dict[str, Any]:
    event = make_envelope("ruleset_reload_failed", ruleset_hash=active_ruleset_hash)
    event["reason"] = reason
    return event


class EventWriter:
    """Writes events as JSONL: one ``write()`` of one line plus newline per event.

    The file is opened in append mode. When ``stdout`` is set, each line is
    also printed to stdout. ``emit`` returns the event's ``event_id`` so
    callers can correlate it (e.g. in an ``X-Gateway-Event-Id`` header).
    """

    def __init__(self, path: str | None = None, *, stdout: bool = False) -> None:
        self._lock = threading.Lock()
        self._file = open(path, "a", encoding="utf-8") if path else None
        self._stdout = stdout
        self.path = path

    def emit(self, event: dict[str, Any]) -> str:
        line = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
        with self._lock:
            if self._file is not None:
                self._file.write(line + "\n")
                self._file.flush()
            if self._stdout:
                print(line, file=sys.stdout, flush=True)
        return event["event_id"]

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    def __enter__(self) -> EventWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
