# Spec Delta

## Purpose

Records every security-relevant gateway decision as a structured JSONL event so detections can be investigated by correlation ID and shipped to a SIEM.

## ADDED Requirements

### Requirement: JSONL event sink
The gateway SHALL write security events as one JSON object per line to a configured file path, and SHALL also write them to stdout when configured to.

#### Scenario: Event written as one line
- **WHEN** a verdict event is produced
- **THEN** exactly one line containing a complete JSON object is appended to the events file
- **ID**: T-SECURITY-EVENTS-001
- **File**: tests/test_events.py::test_event_written_as_single_json_line
- **Command**: `uv run pytest tests/test_events.py::test_event_written_as_single_json_line`
- **Pass**: exit code 0, no skipped tests

### Requirement: Common event envelope
Every event SHALL include `event_id` (unique, time-sortable), `timestamp` (RFC 3339 UTC), `event_type`, `request_id`, `client`, `mode`, and `ruleset_hash`. A field that does not apply to the event type SHALL be null.

#### Scenario: Envelope present
- **WHEN** any event is emitted for a proxied request
- **THEN** it contains all envelope fields, and `request_id` is shared by every event for that request
- **ID**: T-SECURITY-EVENTS-002
- **File**: tests/test_events.py::test_envelope_fields_present_and_request_id_shared
- **Command**: `uv run pytest tests/test_events.py::test_envelope_fields_present_and_request_id_shared`
- **Pass**: exit code 0, no skipped tests

### Requirement: Verdict events
The gateway SHALL emit a `verdict` event for every stage whose verdict is not `allow`. The event SHALL include the stage, endpoint format, model, upstream, the resolved action, `enforced`, and the list of matches (rule id, severity, owasp/atlas tags, canonical field, bounded excerpt).

#### Scenario: Verdict event fields
- **WHEN** a request-stage `flag` verdict is produced
- **THEN** the `verdict` event contains `stage`, `format`, `model`, `upstream`, `action`, `enforced`, and a `matches` list whose entries carry rule id, severity, owasp/atlas tags, canonical field, and excerpt
- **ID**: T-SECURITY-EVENTS-003
- **File**: tests/test_events.py::test_verdict_event_contains_required_fields
- **Command**: `uv run pytest tests/test_events.py::test_verdict_event_contains_required_fields`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Correlation with block response
- **WHEN** a client receives a block response with `X-Gateway-Event-Id: E`
- **THEN** the events file contains a `verdict` event whose `event_id` is `E`
- **ID**: T-SECURITY-EVENTS-004
- **File**: tests/test_proxy.py::test_block_event_id_header_matches_verdict_event
- **Command**: `uv run pytest tests/test_proxy.py::test_block_event_id_header_matches_verdict_event`
- **Pass**: exit code 0, no skipped tests

### Requirement: Request summary events
The gateway SHALL emit one `request_completed` event per API request that reaches the authentication check, including requests rejected by authentication. It SHALL contain the final outcome (`allowed`, `blocked`, `upstream_error`, `auth_failed`), the HTTP status returned, and the latency in milliseconds. An authentication failure SHALL therefore produce both an `auth_failed` event and a `request_completed` event with outcome `auth_failed`, sharing one `request_id`.

#### Scenario: Allowed request logged
- **WHEN** a request is allowed and completes
- **THEN** a `request_completed` event records `allowed` and the status code
- **ID**: T-SECURITY-EVENTS-005
- **File**: tests/test_events.py::test_allowed_request_emits_request_completed
- **Command**: `uv run pytest tests/test_events.py::test_allowed_request_emits_request_completed`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Auth failure summarized
- **WHEN** a request fails authentication
- **THEN** an `auth_failed` event and a `request_completed` event with outcome `auth_failed` and status 401 are recorded with the same `request_id`
- **ID**: T-SECURITY-EVENTS-006
- **File**: tests/test_events.py::test_auth_failure_emits_auth_failed_and_request_completed
- **Command**: `uv run pytest tests/test_events.py::test_auth_failure_emits_auth_failed_and_request_completed`
- **Pass**: exit code 0, no skipped tests

### Requirement: Lifecycle and gap events
The gateway SHALL emit `ruleset_loaded`, `ruleset_reload_failed`, `rule_rejected`, `rule_error`, `uninspected_content`, and `auth_failed` events as specified by the related capabilities.

#### Scenario: Rule rejection logged
- **WHEN** a rule fails validation
- **THEN** a `rule_rejected` event includes the file, line number, rule id (if parseable), and reason
- **ID**: T-SECURITY-EVENTS-007
- **File**: tests/test_rules_loader.py::test_rule_rejected_event_fields
- **Command**: `uv run pytest tests/test_rules_loader.py::test_rule_rejected_event_fields`
- **Pass**: exit code 0, no skipped tests

### Requirement: Secrets never logged
Events SHALL NOT contain client tokens or full request or response bodies. Upstream credentials are covered by llm-proxy: Upstream credential injection ("Upstream credential never returned to client"), which applies to events as well as responses.

#### Scenario: Auth failure event
- **WHEN** an invalid token is presented
- **THEN** the `auth_failed` event contains the source address and header style used, but not the token value
- **ID**: T-SECURITY-EVENTS-008
- **File**: tests/test_events.py::test_auth_failed_event_has_source_and_style_not_token
- **Command**: `uv run pytest tests/test_events.py::test_auth_failed_event_has_source_and_style_not_token`
- **Pass**: exit code 0, no skipped tests
