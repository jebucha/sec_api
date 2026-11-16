# Spec Delta

## MODIFIED Requirements

### Requirement: Starter rule packs
The project SHALL ship starter rule packs covering prompt injection, jailbreak markers, secrets (e.g. cloud keys, VCS tokens, private key headers), PII (including Luhn-validated card numbers), unicode/encoding smuggling (tag characters, zero-width characters, long base64 runs), and a canary-token prompt-leak detector. The starter packs' prompt-injection, secrets, and PII rules SHALL target `tool_results` in addition to `user_text`, so payloads arriving in tool output (file contents, command output, web fetches) are detected by default; the jailbreak, smuggling, and canary-leak packs keep their current targets (design D6). Every starter rule SHALL include passing inline tests. Secrets and PII rules SHALL set `mask_excerpt: true`. The canary-token prompt-leak detector's inline tests reference the documented test-only canary value, so that rule loads only when that value is among the configured canary tokens; a deployment whose canary set omits it sees that one rule rejected with a `rule_rejected` event.

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

#### Scenario: Injection marker in tool result detected
- **WHEN** a request-stage tool result (an OpenAI `role: "tool"` message or an Anthropic `tool_result` block) contains a prompt-injection marker covered by the starter packs
- **THEN** the corresponding starter rule targeting `tool_results` matches at the request stage
- **ID**: T-DETECTION-RULES-021
- **File**: tests/test_starter_packs.py::test_starter_rules_match_injection_in_tool_result
- **Command**: `uv run pytest tests/test_starter_packs.py::test_starter_rules_match_injection_in_tool_result`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Sensitive data in tool result detected
- **WHEN** a request-stage tool result (an OpenAI `role: "tool"` message or an Anthropic `tool_result` block) contains a secret pattern (e.g. an AWS access key id) or PII (e.g. a Luhn-validated card number) covered by the starter packs
- **THEN** the corresponding starter rules targeting `tool_results` match at the request stage with masked excerpts
- **ID**: T-DETECTION-RULES-022
- **File**: tests/test_starter_packs.py::test_starter_rules_match_sensitive_data_in_tool_result
- **Command**: `uv run pytest tests/test_starter_packs.py::test_starter_rules_match_sensitive_data_in_tool_result`
- **Pass**: exit code 0, no skipped tests
