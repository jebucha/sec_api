# Spec Delta

## Purpose

Evaluates active detection rules against canonical content at the request and response stages and turns matches into verdicts, governed by per-rule actions and a global monitor/enforce mode.

## ADDED Requirements

### Requirement: Stage-scoped evaluation
Request-stage rules SHALL be evaluated before the request is forwarded upstream. Response-stage rules SHALL be evaluated against the upstream response. A rule SHALL only be evaluated against the canonical fields listed in its `target`.

#### Scenario: Request rule blocks before upstream
- **GIVEN** mode is `enforce`
- **WHEN** a request-stage rule with action `block` matches `user_text`
- **THEN** the upstream is never contacted
- **ID**: T-DETECTION-ENGINE-001
- **File**: tests/test_proxy.py::test_enforce_request_block_never_contacts_upstream
- **Command**: `uv run pytest tests/test_proxy.py::test_enforce_request_block_never_contacts_upstream`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Target scoping
- **WHEN** a rule targeting only `system` has a pattern that appears only in `user_text`
- **THEN** the rule does not match
- **ID**: T-DETECTION-ENGINE-002
- **File**: tests/test_engine.py::test_rule_only_evaluates_its_target_fields
- **Command**: `uv run pytest tests/test_engine.py::test_rule_only_evaluates_its_target_fields`
- **Pass**: exit code 0, no skipped tests

### Requirement: All matching rules recorded
The engine SHALL evaluate all enabled rules for a stage rather than stopping at the first match. It SHALL record every matching rule in the verdict.

#### Scenario: Multiple matches
- **WHEN** a request matches one `flag` rule and one `block` rule
- **THEN** the verdict lists both rules
- **ID**: T-DETECTION-ENGINE-003
- **File**: tests/test_engine.py::test_all_matching_rules_listed_in_verdict
- **Command**: `uv run pytest tests/test_engine.py::test_all_matching_rules_listed_in_verdict`
- **Pass**: exit code 0, no skipped tests

### Requirement: Verdict resolution by action precedence
The stage verdict SHALL be the most severe action among matching rules, with precedence `block` > `flag` > `log` > `allow`. `allow` applies when nothing matched.

#### Scenario: Flag-only match
- **WHEN** only `flag` rules match
- **THEN** the verdict is `flag`, the traffic proceeds, and a verdict event is recorded
- **ID**: T-DETECTION-ENGINE-004
- **File**: tests/test_engine.py::test_flag_only_match_resolves_to_flag
- **Command**: `uv run pytest tests/test_engine.py::test_flag_only_match_resolves_to_flag`
- **Pass**: exit code 0, no skipped tests

#### Scenario: No match
- **WHEN** no rules match
- **THEN** the verdict is `allow`
- **ID**: T-DETECTION-ENGINE-005
- **File**: tests/test_engine.py::test_no_match_resolves_to_allow
- **Command**: `uv run pytest tests/test_engine.py::test_no_match_resolves_to_allow`
- **Pass**: exit code 0, no skipped tests

### Requirement: Global monitor mode
When the global mode is `monitor`, a `block` verdict SHALL be recorded as would-block in its event, and the traffic SHALL proceed as if the verdict were `flag`.

#### Scenario: Would-block in monitor mode
- **GIVEN** mode is `monitor`
- **WHEN** a request matches a `block` rule
- **THEN** the request is forwarded and the event records `action: block`, `enforced: false`
- **ID**: T-DETECTION-ENGINE-006
- **File**: tests/test_proxy.py::test_monitor_block_forwards_and_records_not_enforced
- **Command**: `uv run pytest tests/test_proxy.py::test_monitor_block_forwards_and_records_not_enforced`
- **Pass**: exit code 0, no skipped tests

### Requirement: Global enforce mode
When the global mode is `enforce`, a `block` verdict SHALL stop the traffic and return a block response.

#### Scenario: Enforced block
- **GIVEN** mode is `enforce`
- **WHEN** a request matches a `block` rule
- **THEN** the client receives a block response and the event records `enforced: true`
- **ID**: T-DETECTION-ENGINE-007
- **File**: tests/test_proxy.py::test_enforce_block_returns_block_and_records_enforced
- **Command**: `uv run pytest tests/test_proxy.py::test_enforce_block_returns_block_and_records_enforced`
- **Pass**: exit code 0, no skipped tests

### Requirement: Match evidence is bounded
For each match, the engine SHALL capture the rule id, the canonical field, and a match excerpt truncated to a configured maximum length (default 200 characters). Full prompt bodies SHALL NOT be captured.

#### Scenario: Long match truncated
- **WHEN** a match spans 5,000 characters
- **THEN** the recorded excerpt is at most the configured maximum length
- **ID**: T-DETECTION-ENGINE-008
- **File**: tests/test_engine.py::test_long_match_excerpt_truncated
- **Command**: `uv run pytest tests/test_engine.py::test_long_match_excerpt_truncated`
- **Pass**: exit code 0, no skipped tests

### Requirement: Sensitive excerpts are masked
For rules with `mask_excerpt: true`, the recorded excerpt SHALL be masked: only the first and last 2 characters are kept, plus the match length. Matched secret or PII values are therefore never written to events.

#### Scenario: Secret match masked
- **GIVEN** a secrets rule with `mask_excerpt: true`
- **WHEN** it matches `AKIAIOSFODNN7EXAMPLE`
- **THEN** the event excerpt is `AK…LE (20 chars)` and the full key appears nowhere in the events file
- **ID**: T-DETECTION-ENGINE-009
- **File**: tests/test_engine.py::test_masked_secret_excerpt_never_in_events
- **Command**: `uv run pytest tests/test_engine.py::test_masked_secret_excerpt_never_in_events`
- **Pass**: exit code 0, no skipped tests

### Requirement: Detection errors fail per mode
If evaluating a rule raises an error, the engine SHALL record a `rule_error` event. In `enforce` mode it SHALL treat the stage as blocked. In `monitor` mode it SHALL let the traffic proceed.

#### Scenario: Engine error in enforce mode
- **GIVEN** mode is `enforce`
- **WHEN** a heuristic raises an exception on some input
- **THEN** the traffic is blocked and a `rule_error` event names the rule
- **ID**: T-DETECTION-ENGINE-010
- **File**: tests/test_engine.py::test_rule_exception_blocks_in_enforce_and_emits_rule_error
- **Command**: `uv run pytest tests/test_engine.py::test_rule_exception_blocks_in_enforce_and_emits_rule_error`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Engine error in monitor mode
- **GIVEN** mode is `monitor`
- **WHEN** a heuristic raises an exception on some input
- **THEN** the traffic proceeds and a `rule_error` event names the rule
- **ID**: T-DETECTION-ENGINE-011
- **File**: tests/test_engine.py::test_rule_exception_passes_in_monitor_and_emits_rule_error
- **Command**: `uv run pytest tests/test_engine.py::test_rule_exception_passes_in_monitor_and_emits_rule_error`
- **Pass**: exit code 0, no skipped tests
