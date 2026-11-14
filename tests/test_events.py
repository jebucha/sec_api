"""Tests for the event envelope and JSONL writer (security-events spec)."""

from __future__ import annotations

import json

import httpx
from starlette.testclient import TestClient

from helpers import (
    CHAT_BODY,
    auth_headers,
    ensure_rules_dir,
    events_of_type,
    make_rule,
    make_ruleset,
    make_test_config,
    make_upstream_handler,
    read_events,
)
from llmgw.app import create_app
from llmgw.engine import evaluate
from llmgw.events import (
    ENVELOPE_FIELDS,
    EventWriter,
    RequestContext,
    make_envelope,
    new_ulid,
    verdict_event,
)
from llmgw.formats.canonical import Canonical


def test_event_written_as_single_json_line(tmp_path):
    """T-SECURITY-EVENTS-001: exactly one line containing a complete JSON object."""
    path = tmp_path / "events.jsonl"
    event = make_envelope("verdict", request_id="01TEST")
    with EventWriter(str(path)) as writer:
        writer.emit(event)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == event


def test_envelope_fields_present_and_request_id_shared(tmp_path):
    """T-SECURITY-EVENTS-002: all envelope fields present; request_id shared."""
    ctx = RequestContext(
        request_id=new_ulid(),
        client="opencode-laptop",
        mode="monitor",
        ruleset_hash="abc123",
    )
    events = [
        ctx.envelope("verdict"),
        ctx.envelope("request_completed"),
    ]

    assert events[0]["event_id"] != events[1]["event_id"]
    assert all(e["request_id"] == ctx.request_id for e in events)
    for event in events:
        for field in ENVELOPE_FIELDS:
            assert field in event
        # RFC 3339 UTC: ends in Z, parseable as an ISO-8601 date
        assert event["timestamp"].endswith("Z")
        assert event["timestamp"][:10] and "T" in event["timestamp"]
        assert event["client"] == "opencode-laptop"
        assert event["mode"] == "monitor"
        assert event["ruleset_hash"] == "abc123"


def test_envelope_nulls_for_n_a_fields():
    event = make_envelope("ruleset_loaded")
    assert event["request_id"] is None
    assert event["client"] is None
    assert event["mode"] is None
    assert event["ruleset_hash"] is None


def test_verdict_event_contains_required_fields():
    """T-SECURITY-EVENTS-003: verdict event carries all required fields."""
    ruleset = make_ruleset(
        make_rule(
            rule_id="PI-001",
            matches="override",
            action="flag",
            severity="high",
            owasp=("LLM01:2025",),
            atlas=("ATLAS-0002",),
        )
    )
    verdict = evaluate(
        ruleset,
        Canonical(user_text=("please override the policy",)),
        stage="request",
        mode="monitor",
    )
    ctx = RequestContext(
        request_id="req-1", client="opencode-laptop", mode="monitor", ruleset_hash="h1"
    )
    event = verdict_event(
        ctx,
        stage="request",
        format="openai",
        model="qwen3-32b",
        upstream="gpu-box",
        action=verdict.action,
        enforced=verdict.enforced,
        matches=verdict.matches,
    )
    assert event["event_type"] == "verdict"
    assert event["stage"] == "request"
    assert event["format"] == "openai"
    assert event["model"] == "qwen3-32b"
    assert event["upstream"] == "gpu-box"
    assert event["action"] == "flag"
    assert event["enforced"] is False
    (match,) = event["matches"]
    assert match["rule_id"] == "PI-001"
    assert match["severity"] == "high"
    assert match["owasp"] == ["LLM01:2025"]
    assert match["atlas"] == ["ATLAS-0002"]
    assert match["field"] == "user_text"
    assert match["excerpt"] == "override"


def test_redaction_guard_catches_injected_secret():
    """2.2: the guard fails when a secret value is injected into an event."""
    from helpers import scan_events_for_secrets

    secret = "super-secret-token"
    events = [make_envelope("verdict", request_id="x")]
    assert scan_events_for_secrets(events, [secret]) == []

    events[0]["client"] = secret  # inject the secret into an event
    assert scan_events_for_secrets(events, [secret]) == [secret]


# --- request summary events through the app -----------------------------------


def _client(tmp_path):
    """An in-process gateway; returns (TestClient, events path)."""
    config = make_test_config(tmp_path)
    ensure_rules_dir(config)
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    return TestClient(app), config.events.path


def test_allowed_request_emits_request_completed(tmp_path):
    """T-SECURITY-EVENTS-005: an allowed request records request_completed/allowed."""
    client, events_path = _client(tmp_path)
    with client as c:
        response = c.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers())
    assert response.status_code == 200
    completed = events_of_type(read_events(events_path), "request_completed")
    assert len(completed) == 1
    assert completed[0]["outcome"] == "allowed"
    assert completed[0]["status"] == 200
    assert completed[0]["request_id"] == response.headers["x-gateway-request-id"]


def test_auth_failure_emits_auth_failed_and_request_completed(tmp_path):
    """T-SECURITY-EVENTS-006: auth failure records both events under one request_id."""
    client, events_path = _client(tmp_path)
    with client as c:
        response = c.post(
            "/v1/chat/completions", json=CHAT_BODY, headers=auth_headers(token="bad-token")
        )
    assert response.status_code == 401
    events = read_events(events_path)
    auth_failed = events_of_type(events, "auth_failed")
    completed = events_of_type(events, "request_completed")
    assert len(auth_failed) == 1
    assert len(completed) == 1
    assert auth_failed[0]["request_id"] == completed[0]["request_id"]
    assert completed[0]["outcome"] == "auth_failed"
    assert completed[0]["status"] == 401


def test_auth_failed_event_has_source_and_style_not_token(tmp_path):
    """T-SECURITY-EVENTS-008: the event names source and style, never the token."""
    wrong = "the-wrong-token-value"
    client, events_path = _client(tmp_path)
    with client as c:
        c.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers("x-api-key", wrong))
    auth_failed = events_of_type(read_events(events_path), "auth_failed")
    assert len(auth_failed) == 1
    event = auth_failed[0]
    assert event["source"]
    assert event["style"] == "x-api-key"
    assert wrong not in str(event)
