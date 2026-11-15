# Spec Delta

## Purpose

Provides a labeled JSONL test corpus and a replay tool so detection rules can be measured for detection and false-positive rates before and after every rule change.

## ADDED Requirements

### Requirement: Corpus format
Test corpora SHALL be JSONL files where each line has `id`, `text`, `label` (`malicious`|`benign`), and `category`, plus optional `expected_rules` (list of rule ids) and `field` (canonical field to place the text in, default `user_text`).

#### Scenario: Invalid corpus line
- **WHEN** a corpus line lacks `label`
- **THEN** the replay tool reports the line as invalid and excludes it from metrics
- **ID**: T-DETECTION-TESTING-001
- **File**: tests/test_replay.py::test_invalid_corpus_line_reported_and_excluded
- **Command**: `uv run pytest tests/test_replay.py::test_invalid_corpus_line_reported_and_excluded`
- **Pass**: exit code 0, no skipped tests

### Requirement: Corpus stage and format fields
Corpus entries SHALL accept optional `stage` (`request`|`response`, default `request`), which is the stage the entry is evaluated at. A response-stage entry places `text` in the response canonical structure, so response rules such as `prompt-leak` can be exercised. Entries SHALL also accept optional `format` (`openai`|`anthropic`, default `openai`), which is the wire format used in live mode and is ignored offline.

#### Scenario: Response-stage entry
- **WHEN** an entry has `stage: response` and `field: assistant_text`
- **THEN** only response-stage rules are evaluated against it
- **ID**: T-DETECTION-TESTING-003
- **File**: tests/test_replay.py::test_response_stage_entry_evaluates_response_rules_only
- **Command**: `uv run pytest tests/test_replay.py::test_response_stage_entry_evaluates_response_rules_only`
- **Pass**: exit code 0, no skipped tests

### Requirement: Offline replay
The replay tool SHALL evaluate corpus entries against a rules directory without a running gateway or upstream, using the same rule loading and evaluation behavior as the gateway. For each entry it SHALL build a canonical structure directly, with `text` in `field`, and evaluate the rules for the entry's `stage`.

#### Scenario: Offline run
- **WHEN** the tool is run with a corpus file and a rules directory
- **THEN** it produces results without any network connection
- **ID**: T-DETECTION-TESTING-002
- **File**: tests/test_replay.py::test_offline_run_makes_no_network_calls
- **Command**: `uv run pytest tests/test_replay.py::test_offline_run_makes_no_network_calls`
- **Pass**: exit code 0, no skipped tests

### Requirement: Live replay
The replay tool SHALL optionally send request-stage corpus entries through a running gateway as real requests (in the entry's `format`), using a supplied client token. It SHALL correlate each response with its events by the `X-Gateway-Request-Id` response header, which matches the `request_id` of every event for that request. Response-stage entries SHALL be reported as skipped in live mode.

#### Scenario: Live run through monitor-mode gateway
- **WHEN** the tool is run in live mode against a monitor-mode gateway
- **THEN** each request-stage entry is sent as a chat request, and its results are derived from the events whose `request_id` equals the response's `X-Gateway-Request-Id`
- **ID**: T-DETECTION-TESTING-004
- **File**: tests/test_replay.py::test_live_mode_correlates_by_request_id_in_monitor_mode
- **Command**: `uv run pytest tests/test_replay.py::test_live_mode_correlates_by_request_id_in_monitor_mode`
- **Pass**: exit code 0, no skipped tests

### Requirement: Detection metrics report
The tool SHALL report, overall and per category and per rule: true positives, false positives, false negatives, true negatives, detection rate, and false-positive rate. It SHALL output both a human-readable table and a JSON report.

#### Scenario: False positive surfaced
- **WHEN** a `benign` entry matches rule `PI-001`
- **THEN** the report counts a false positive against `PI-001` and lists the entry id
- **ID**: T-DETECTION-TESTING-006
- **File**: tests/test_replay.py::test_benign_match_counted_as_false_positive
- **Command**: `uv run pytest tests/test_replay.py::test_benign_match_counted_as_false_positive`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Expected rule missed
- **WHEN** a `malicious` entry declares `expected_rules: ["PI-001"]` and `PI-001` does not match
- **THEN** the report lists the entry as a miss for `PI-001`
- **ID**: T-DETECTION-TESTING-007
- **File**: tests/test_replay.py::test_expected_rule_miss_listed
- **Command**: `uv run pytest tests/test_replay.py::test_expected_rule_miss_listed`
- **Pass**: exit code 0, no skipped tests

### Requirement: Overall and per-category metric definitions
An entry SHALL count as *detected* when at least one rule matches it, whatever the rule's action. Overall and per category, TP = malicious and detected, FN = malicious and not detected, FP = benign and detected, and TN = benign and not detected. Detection rate SHALL be TP / (TP + FN) and false-positive rate FP / (FP + TN). A rate whose denominator is 0 SHALL be reported as null.

#### Scenario: Metrics computed from known counts
- **WHEN** a corpus with 3 malicious entries (2 detected) and 4 benign entries (1 detected) is replayed
- **THEN** the overall report shows TP 2, FN 1, FP 1, TN 3, detection rate 0.667, and false-positive rate 0.25
- **ID**: T-DETECTION-TESTING-005
- **File**: tests/test_replay.py::test_metrics_match_known_counts
- **Command**: `uv run pytest tests/test_replay.py::test_metrics_match_known_counts`
- **Pass**: exit code 0, no skipped tests

### Requirement: Per-rule metric definitions
For each rule R, positives SHALL be malicious entries whose `expected_rules` contains R, and negatives SHALL be all benign entries. TP = positive and R matched, FN = positive and R not matched, FP = negative and R matched, and TN = negative and R not matched. Malicious entries that do not list R SHALL be excluded from R's counts. A match of R on one of them SHALL be listed as an unexpected match. Rates use the same formulas as the overall metrics.

#### Scenario: Unlisted malicious entry excluded from rule counts
- **WHEN** a `malicious` entry with `expected_rules: ["PI-002"]` also matches `PI-001`
- **THEN** `PI-001`'s TP/FP/FN/TN counts are unchanged and the entry is listed as an unexpected match for `PI-001`
- **ID**: T-DETECTION-TESTING-010
- **File**: tests/test_replay.py::test_unlisted_malicious_match_reported_as_unexpected
- **Command**: `uv run pytest tests/test_replay.py::test_unlisted_malicious_match_reported_as_unexpected`
- **Pass**: exit code 0, no skipped tests

### Requirement: CI-friendly thresholds
The tool SHALL accept a minimum detection rate and a maximum false-positive rate. It SHALL exit non-zero when either threshold is violated.

#### Scenario: Threshold violation
- **WHEN** the false-positive rate exceeds the configured maximum
- **THEN** the tool exits with a non-zero status
- **ID**: T-DETECTION-TESTING-008
- **File**: tests/test_replay.py::test_fp_threshold_violation_exits_non_zero
- **Command**: `uv run pytest tests/test_replay.py::test_fp_threshold_violation_exits_non_zero`
- **Pass**: exit code 0, no skipped tests

### Requirement: Starter corpus
The project SHALL ship a starter corpus with malicious and benign examples for every starter rule pack category, including near-miss benign examples designed to probe false positives. Prompt-leak entries SHALL use a documented, test-only canary value, which is never used in a real deployment. The replay tool SHALL accept that value through a `--canaries` option, which sets the canary heuristic's input for the run.

#### Scenario: Starter packs against starter corpus
- **WHEN** the starter corpus is replayed against the starter packs with the documented test canary supplied via `--canaries`
- **THEN** every starter pack category has at least one true positive and at least one benign entry evaluated
- **ID**: T-DETECTION-TESTING-009
- **File**: tests/test_starter_packs.py::test_starter_corpus_covers_every_category
- **Command**: `uv run pytest tests/test_starter_packs.py::test_starter_corpus_covers_every_category`
- **Pass**: exit code 0, no skipped tests
