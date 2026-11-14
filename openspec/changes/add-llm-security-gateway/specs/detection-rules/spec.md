# Spec Delta

## Purpose

Defines detection logic as machine-readable JSONL rule packs, and governs how rules are validated, self-tested, loaded, and hot-reloaded so that detection content can be versioned and trusted like code.

## ADDED Requirements

### Requirement: JSONL rule packs
Rules SHALL be loaded from `*.jsonl` files in a configured rules directory, one JSON rule object per line. Blank lines and lines beginning with `//` SHALL be ignored.

#### Scenario: Pack with several rules
- **WHEN** the rules directory contains a `.jsonl` file with three valid rule lines
- **THEN** all three rules are active
- **ID**: T-DETECTION-RULES-001
- **File**: tests/test_rules_loader.py::test_pack_with_three_rules_all_active
- **Command**: `uv run pytest tests/test_rules_loader.py::test_pack_with_three_rules_all_active`
- **Pass**: exit code 0, no skipped tests

### Requirement: Rule schema
Each rule SHALL conform to a published JSON Schema with required fields `id` (unique), `name`, `version`, `stage` (`request`|`response`), `target` (non-empty list of canonical field names), `type` (`regex`|`keyword`|`heuristic`), `severity` (`low`|`medium`|`high`|`critical`), `action` (`block`|`flag`|`log`), and `enabled`. Optional fields are `description`, `owasp`, `atlas`, `tags`, `mask_excerpt`, and `tests`.

#### Scenario: Schema violation
- **WHEN** a rule line is missing `action` or has an unknown `stage` value
- **THEN** that rule is rejected and the other rules in the file still load
- **ID**: T-DETECTION-RULES-002
- **File**: tests/test_rules_loader.py::test_schema_violation_rejects_only_that_rule
- **Command**: `uv run pytest tests/test_rules_loader.py::test_schema_violation_rejects_only_that_rule`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Duplicate id
- **WHEN** two rules share the same `id`
- **THEN** both are rejected and a `rule_rejected` event names the duplicate id
- **ID**: T-DETECTION-RULES-004
- **File**: tests/test_rules_loader.py::test_duplicate_id_rejects_both
- **Command**: `uv run pytest tests/test_rules_loader.py::test_duplicate_id_rejects_both`
- **Pass**: exit code 0, no skipped tests

### Requirement: Type-specific rule payloads
The rule schema SHALL require the payload field for each rule type. `regex` rules require `pattern` (string). `keyword` rules require `keywords` (non-empty list of strings) and accept optional `case_sensitive` (boolean, default `false`). `heuristic` rules require `heuristic` (built-in heuristic name) and accept optional `params` (object, validated by the named heuristic).

#### Scenario: Missing type payload
- **WHEN** a rule has `type: regex` but no `pattern`, or `type: keyword` but no `keywords`
- **THEN** that rule is rejected with a `rule_rejected` event citing the missing field
- **ID**: T-DETECTION-RULES-003
- **File**: tests/test_rules_loader.py::test_missing_type_payload_rejected
- **Command**: `uv run pytest tests/test_rules_loader.py::test_missing_type_payload_rejected`
- **Pass**: exit code 0, no skipped tests

### Requirement: Linear-time regex patterns
Rules of type `regex` SHALL be compiled with a linear-time (RE2-semantics) engine. Patterns that the engine cannot compile, including backreferences and lookarounds, SHALL be rejected.

#### Scenario: Unsupported construct
- **WHEN** a regex rule's pattern uses a lookahead `(?=...)`
- **THEN** the rule is rejected with a `rule_rejected` event citing compile failure
- **ID**: T-DETECTION-RULES-005
- **File**: tests/test_rules_loader.py::test_lookahead_rejected_with_compile_failure
- **Command**: `uv run pytest tests/test_rules_loader.py::test_lookahead_rejected_with_compile_failure`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Pathological pattern is safe
- **WHEN** an accepted rule pattern such as `(a+)+$` is evaluated against a 100,000-character adversarial input (`"a" * 99_999 + "!"`)
- **THEN** evaluation completes in under 1 second
- **ID**: T-DETECTION-RULES-006
- **File**: tests/test_rules_loader.py::test_pathological_pattern_completes_under_one_second
- **Command**: `uv run pytest tests/test_rules_loader.py::test_pathological_pattern_completes_under_one_second`
- **Pass**: exit code 0, no skipped tests

### Requirement: Keyword and heuristic rule types
`keyword` rules SHALL match when any listed keyword appears (case-insensitive by default). `heuristic` rules SHALL name a built-in heuristic and its parameters. The set of built-in heuristics is fixed by the gateway version.

#### Scenario: Keyword case-insensitive by default
- **WHEN** a keyword rule lists `ignore previous instructions` and the text contains `IGNORE PREVIOUS INSTRUCTIONS`
- **THEN** the rule matches
- **ID**: T-DETECTION-RULES-007
- **File**: tests/test_rules_loader.py::test_keyword_rule_case_insensitive_by_default
- **Command**: `uv run pytest tests/test_rules_loader.py::test_keyword_rule_case_insensitive_by_default`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Unknown heuristic
- **WHEN** a heuristic rule names a heuristic the gateway does not implement
- **THEN** the rule is rejected
- **ID**: T-DETECTION-RULES-008
- **File**: tests/test_rules_loader.py::test_unknown_heuristic_rejected
- **Command**: `uv run pytest tests/test_rules_loader.py::test_unknown_heuristic_rejected`
- **Pass**: exit code 0, no skipped tests

### Requirement: Inline rule self-tests
When a rule has `tests.match` and/or `tests.no_match` examples, the loader SHALL evaluate the rule against each example. It SHALL reject the rule if any `match` example fails to match or any `no_match` example matches.

#### Scenario: Failing self-test
- **WHEN** a rule's `tests.no_match` contains a string the rule matches
- **THEN** the rule is rejected and the event identifies the failing example index
- **ID**: T-DETECTION-RULES-009
- **File**: tests/test_rules_loader.py::test_failing_no_match_example_rejects_with_index
- **Command**: `uv run pytest tests/test_rules_loader.py::test_failing_no_match_example_rejects_with_index`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Passing self-test
- **WHEN** all of a rule's test examples behave as declared
- **THEN** the rule is loaded
- **ID**: T-DETECTION-RULES-010
- **File**: tests/test_rules_loader.py::test_passing_self_tests_load_rule
- **Command**: `uv run pytest tests/test_rules_loader.py::test_passing_self_tests_load_rule`
- **Pass**: exit code 0, no skipped tests

### Requirement: Disabled rules
Rules with `enabled: false` SHALL be validated but not evaluated.

#### Scenario: Disabled rule ignored
- **WHEN** a disabled rule's pattern appears in a request
- **THEN** no verdict is produced for that rule
- **ID**: T-DETECTION-RULES-011
- **File**: tests/test_engine.py::test_disabled_rule_not_evaluated
- **Command**: `uv run pytest tests/test_engine.py::test_disabled_rule_not_evaluated`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Disabled rule still validated
- **WHEN** a rule with `enabled: false` violates the schema
- **THEN** it is rejected with a `rule_rejected` event
- **ID**: T-DETECTION-RULES-012
- **File**: tests/test_rules_loader.py::test_disabled_rule_still_schema_validated
- **Command**: `uv run pytest tests/test_rules_loader.py::test_disabled_rule_still_schema_validated`
- **Pass**: exit code 0, no skipped tests

### Requirement: Ruleset identity
Each successfully loaded ruleset SHALL have a deterministic content hash over its active rules. That hash SHALL be included in every security event.

#### Scenario: Same files, same hash
- **WHEN** an unchanged rules directory is loaded twice
- **THEN** both loads report the same ruleset hash
- **ID**: T-DETECTION-RULES-013
- **File**: tests/test_rules_loader.py::test_same_directory_same_hash
- **Command**: `uv run pytest tests/test_rules_loader.py::test_same_directory_same_hash`
- **Pass**: exit code 0, no skipped tests

### Requirement: Hot reload with atomic swap
The gateway SHALL reload rules when files in the rules directory change or when it receives SIGHUP. It SHALL build and validate the new ruleset fully before atomically replacing the active one. In-flight requests SHALL complete with the ruleset they started with.

#### Scenario: Successful reload
- **WHEN** a rule file is edited and saved with valid content
- **THEN** new requests are evaluated with the new rules, and a `ruleset_loaded` event reports the new hash and the counts of accepted and rejected rules
- **ID**: T-DETECTION-RULES-014
- **File**: tests/test_reload.py::test_file_edit_reloads_and_emits_ruleset_loaded
- **Command**: `uv run pytest tests/test_reload.py::test_file_edit_reloads_and_emits_ruleset_loaded`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Failed reload keeps old rules
- **WHEN** a reload fails as a whole (e.g. the rules directory is unreadable)
- **THEN** the previous ruleset stays active and a `ruleset_reload_failed` event is emitted
- **ID**: T-DETECTION-RULES-015
- **File**: tests/test_reload.py::test_failed_reload_keeps_previous_ruleset
- **Command**: `uv run pytest tests/test_reload.py::test_failed_reload_keeps_previous_ruleset`
- **Pass**: exit code 0, no skipped tests

#### Scenario: In-flight request keeps its ruleset
- **WHEN** a reload completes while a request is being processed
- **THEN** that request's events carry the ruleset hash captured at its start
- **ID**: T-DETECTION-RULES-016
- **File**: tests/test_reload.py::test_in_flight_request_keeps_original_hash
- **Command**: `uv run pytest tests/test_reload.py::test_in_flight_request_keeps_original_hash`
- **Pass**: exit code 0, no skipped tests

### Requirement: Startup with invalid rules
At startup the gateway SHALL start with whatever rules are valid, emitting `rule_rejected` events for the rest. It SHALL refuse to start only if the rules directory itself cannot be read.

#### Scenario: One bad rule at startup
- **WHEN** the gateway starts with one invalid rule among valid ones
- **THEN** it starts serving with the valid rules
- **ID**: T-DETECTION-RULES-017
- **File**: tests/test_app.py::test_startup_with_one_invalid_rule_serves_valid_rules
- **Command**: `uv run pytest tests/test_app.py::test_startup_with_one_invalid_rule_serves_valid_rules`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Unreadable rules directory at startup
- **WHEN** the gateway starts and the configured rules directory cannot be read
- **THEN** startup fails with an error naming the directory
- **ID**: T-DETECTION-RULES-018
- **File**: tests/test_app.py::test_startup_refuses_unreadable_rules_dir
- **Command**: `uv run pytest tests/test_app.py::test_startup_refuses_unreadable_rules_dir`
- **Pass**: exit code 0, no skipped tests

### Requirement: Starter rule packs
The project SHALL ship starter rule packs covering prompt injection, jailbreak markers, secrets (e.g. cloud keys, VCS tokens, private key headers), PII (including Luhn-validated card numbers), unicode/encoding smuggling (tag characters, zero-width characters, long base64 runs), and a canary-token prompt-leak detector. Every starter rule SHALL include passing inline tests. Secrets and PII rules SHALL set `mask_excerpt: true`. The canary-token prompt-leak detector's inline tests reference the documented test-only canary value, so that rule loads only when that value is among the configured canary tokens (design D11); a deployment whose canary set omits it sees that one rule rejected with a `rule_rejected` event.

#### Scenario: Starter packs load clean
- **GIVEN** the configured canary tokens include the documented test-only canary
- **WHEN** the gateway starts with only the starter packs
- **THEN** zero rules are rejected
- **ID**: T-DETECTION-RULES-019
- **File**: tests/test_starter_packs.py::test_starter_packs_load_with_zero_rejections
- **Command**: `uv run pytest tests/test_starter_packs.py::test_starter_packs_load_with_zero_rejections`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Canary leak detected
- **GIVEN** a configured canary token is planted in a system prompt
- **WHEN** an upstream response contains that canary token
- **THEN** the prompt-leak rule matches at the response stage
- **ID**: T-DETECTION-RULES-020
- **File**: tests/test_starter_packs.py::test_canary_in_response_matches_prompt_leak_rule
- **Command**: `uv run pytest tests/test_starter_packs.py::test_canary_in_response_matches_prompt_leak_rule`
- **Pass**: exit code 0, no skipped tests
