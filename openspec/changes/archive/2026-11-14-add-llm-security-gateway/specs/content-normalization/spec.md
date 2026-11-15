# Spec Delta

## Purpose

Converts OpenAI-compatible and Anthropic-compatible chat requests and responses into one canonical inspection structure, so detection rules are written once and behave identically regardless of wire format.

## ADDED Requirements

### Requirement: Canonical inspection fields
The gateway SHALL normalize every chat request and response into canonical fields: `system`, `user_text`, `assistant_text`, `tool_calls`, `tool_results`, and `uninspected`. Rules SHALL target canonical field names, never wire-format paths.

#### Scenario: Same content, same canonical form
- **WHEN** semantically identical chat requests arrive at `/v1/chat/completions` and `/v1/messages`
- **THEN** both produce the same `system` and `user_text` canonical values
- **ID**: T-CONTENT-NORMALIZATION-001
- **File**: tests/test_parity.py::test_identical_requests_yield_identical_canonical_fields
- **Command**: `uv run pytest tests/test_parity.py::test_identical_requests_yield_identical_canonical_fields`
- **Pass**: exit code 0, no skipped tests

### Requirement: System prompt extraction from both formats
The `system` field SHALL contain system instructions from OpenAI `system`/`developer` role messages and from the Anthropic top-level `system` field (string or text blocks).

#### Scenario: OpenAI system message
- **WHEN** an OpenAI-format request contains a message with role `system`
- **THEN** its text is in the canonical `system` field
- **ID**: T-CONTENT-NORMALIZATION-002
- **File**: tests/test_formats.py::test_openai_system_message_extracted
- **Command**: `uv run pytest tests/test_formats.py::test_openai_system_message_extracted`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Anthropic top-level system
- **WHEN** an Anthropic-format request has a top-level `system` given as an array of text blocks
- **THEN** the concatenated block text is in the canonical `system` field
- **ID**: T-CONTENT-NORMALIZATION-003
- **File**: tests/test_formats.py::test_anthropic_system_blocks_extracted
- **Command**: `uv run pytest tests/test_formats.py::test_anthropic_system_blocks_extracted`
- **Pass**: exit code 0, no skipped tests

### Requirement: Text extraction from content parts
Text in both string content and structured content parts/blocks (`type: "text"`) SHALL be extracted into the role-appropriate canonical field.

#### Scenario: Multi-part user content
- **WHEN** a user message's content is an array of text parts
- **THEN** every part's text appears in `user_text`
- **ID**: T-CONTENT-NORMALIZATION-004
- **File**: tests/test_formats.py::test_multipart_user_content_extracted
- **Command**: `uv run pytest tests/test_formats.py::test_multipart_user_content_extracted`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Prior assistant turns
- **WHEN** a request includes earlier assistant messages in its history
- **THEN** their text appears in `assistant_text` for request-stage inspection
- **ID**: T-CONTENT-NORMALIZATION-005
- **File**: tests/test_formats.py::test_prior_assistant_turns_in_assistant_text
- **Command**: `uv run pytest tests/test_formats.py::test_prior_assistant_turns_in_assistant_text`
- **Pass**: exit code 0, no skipped tests

### Requirement: Response normalization
Response-stage inspection SHALL normalize upstream responses, both non-streaming bodies and fully accumulated streams, so that generated text is in `assistant_text`.

#### Scenario: Accumulated stream
- **WHEN** a streamed response completes
- **THEN** the concatenation of all text deltas is the canonical `assistant_text`
- **ID**: T-CONTENT-NORMALIZATION-006
- **File**: tests/test_formats.py::test_accumulated_stream_deltas_form_assistant_text
- **Command**: `uv run pytest tests/test_formats.py::test_accumulated_stream_deltas_form_assistant_text`
- **Pass**: exit code 0, no skipped tests

### Requirement: Duplicate JSON keys rejected
The gateway SHALL reject a chat request whose JSON body contains a duplicate key at the top level or within any message object with a provider-shaped 400 error, and SHALL NOT contact the upstream. This prevents the canonical parser and the upstream from interpreting the same bytes differently.

#### Scenario: Duplicate top-level key
- **WHEN** a request body contains two `messages` keys
- **THEN** the gateway responds 400 with a provider-shaped error and contacts no upstream
- **ID**: T-CONTENT-NORMALIZATION-007
- **File**: tests/test_formats.py::test_duplicate_top_level_key_rejected_400
- **Command**: `uv run pytest tests/test_formats.py::test_duplicate_top_level_key_rejected_400`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Duplicate key inside a message
- **WHEN** a message object contains two `content` keys
- **THEN** the gateway responds 400 with a provider-shaped error and contacts no upstream
- **ID**: T-CONTENT-NORMALIZATION-008
- **File**: tests/test_formats.py::test_duplicate_message_key_rejected_400
- **Command**: `uv run pytest tests/test_formats.py::test_duplicate_message_key_rejected_400`
- **Pass**: exit code 0, no skipped tests

### Requirement: Uninspected content is flagged
Content the parser does not inspect in this version (tool calls, tool results, request tool definitions such as OpenAI `tools[]` and Anthropic `tools[]`, images, unknown part types) SHALL be recorded in `uninspected` with its location and type. The gateway SHALL emit an `uninspected_content` event for it.

#### Scenario: Tool-use block in v1
- **WHEN** a request contains an Anthropic `tool_use` or `tool_result` block or an OpenAI `tool_calls` entry
- **THEN** the request continues with an `uninspected_content` event naming the content type
- **ID**: T-CONTENT-NORMALIZATION-009
- **File**: tests/test_formats.py::test_tool_blocks_emit_uninspected_content_event
- **Command**: `uv run pytest tests/test_formats.py::test_tool_blocks_emit_uninspected_content_event`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Tool definitions in v1
- **WHEN** a request contains a non-empty `tools` array
- **THEN** the request continues with an `uninspected_content` event of type `tool_definitions`
- **ID**: T-CONTENT-NORMALIZATION-010
- **File**: tests/test_formats.py::test_tool_definitions_emit_uninspected_content_event
- **Command**: `uv run pytest tests/test_formats.py::test_tool_definitions_emit_uninspected_content_event`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Unknown part type
- **WHEN** a content part has a `type` the parser does not recognize
- **THEN** it is recorded as uninspected and does not cause the request to fail parsing
- **ID**: T-CONTENT-NORMALIZATION-011
- **File**: tests/test_formats.py::test_unknown_part_type_recorded_uninspected
- **Command**: `uv run pytest tests/test_formats.py::test_unknown_part_type_recorded_uninspected`
- **Pass**: exit code 0, no skipped tests

### Requirement: Optional fail-closed on uninspected content
When `uninspected_policy` is set to `fail_closed`, any request or response with uninspected content SHALL be blocked regardless of the global mode.

#### Scenario: Fail-closed policy
- **GIVEN** `uninspected_policy` is `fail_closed`
- **WHEN** a request contains an image part
- **THEN** the gateway returns a provider-shaped block response and does not contact the upstream
- **ID**: T-CONTENT-NORMALIZATION-012
- **File**: tests/test_proxy.py::test_fail_closed_blocks_image_without_upstream_contact
- **Command**: `uv run pytest tests/test_proxy.py::test_fail_closed_blocks_image_without_upstream_contact`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Default flag policy
- **GIVEN** `uninspected_policy` is unset or `flag`
- **WHEN** a request contains an image part
- **THEN** the request is processed normally and an `uninspected_content` event is recorded
- **ID**: T-CONTENT-NORMALIZATION-013
- **File**: tests/test_proxy.py::test_flag_policy_forwards_image_and_records_event
- **Command**: `uv run pytest tests/test_proxy.py::test_flag_policy_forwards_image_and_records_event`
- **Pass**: exit code 0, no skipped tests
