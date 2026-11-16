# Spec Delta

## MODIFIED Requirements

### Requirement: Canonical inspection fields
The gateway SHALL normalize every chat request and response into canonical fields: `system`, `user_text`, `assistant_text`, `reasoning_text`, `tool_calls`, `tool_results`, and `uninspected`. Rules SHALL target canonical field names, never wire-format paths.

#### Scenario: Same content, same canonical form
- **WHEN** semantically identical chat requests arrive at `/v1/chat/completions` and `/v1/messages`
- **THEN** both produce the same `system` and `user_text` canonical values
- **ID**: T-CONTENT-NORMALIZATION-001
- **File**: tests/test_parity.py::test_identical_requests_yield_identical_canonical_fields
- **Command**: `uv run pytest tests/test_parity.py::test_identical_requests_yield_identical_canonical_fields`
- **Pass**: exit code 0, no skipped tests

### Requirement: Uninspected content is flagged
Content the parser does not inspect (request tool definitions such as OpenAI `tools[]` and Anthropic `tools[]`, images, unknown part types, legacy `function_call` fields, malformed tool content, and stream issues) SHALL be recorded in `uninspected` with its location and type, and the gateway SHALL emit an `uninspected_content` event for it. Well-formed tool calls and tool results are NOT uninspected: they are extracted into the canonical `tool_calls` and `tool_results` fields as specified by "Tool call extraction" and "Tool result extraction"; malformed tool content (non-list `tool_calls`, non-object call entries, and stream fragments that cannot be accumulated) remains uninspected. For each stage, the gateway SHALL emit at most one `uninspected_content` event per unique combination of kind, location, and detail, and SHALL include a `count` field in each event giving the number of gaps aggregated into it.

#### Scenario: Tool-use block in v1
- **WHEN** a request contains an Anthropic `tool_use` or `tool_result` block or an OpenAI `tool_calls` entry or `role: "tool"` message
- **THEN** its content appears in the canonical `tool_calls` or `tool_results` field and no `uninspected_content` event of kind `tool_call` or `tool_result` is emitted
- **ID**: T-CONTENT-NORMALIZATION-009
- **File**: tests/test_formats.py::test_tool_blocks_no_longer_emit_uninspected_events
- **Command**: `uv run pytest tests/test_formats.py::test_tool_blocks_no_longer_emit_uninspected_events`
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

#### Scenario: Duplicate gaps collapse into one event
- **WHEN** a streamed response produces 20 uninspected gaps that share the same kind, location, and detail (e.g. one non-text delta field per chunk)
- **THEN** the gateway emits exactly one `uninspected_content` event for that combination at that stage, with `count` 20
- **ID**: T-CONTENT-NORMALIZATION-031
- **File**: tests/test_proxy.py::test_duplicate_gaps_collapse_into_one_event_with_count
- **Command**: `uv run pytest tests/test_proxy.py::test_duplicate_gaps_collapse_into_one_event_with_count`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Distinct gaps remain separate events
- **WHEN** a stage produces two uninspected gaps with different locations
- **THEN** the gateway emits two `uninspected_content` events, each with `count` 1
- **ID**: T-CONTENT-NORMALIZATION-032
- **File**: tests/test_proxy.py::test_distinct_gaps_emit_separate_events_with_count_one
- **Command**: `uv run pytest tests/test_proxy.py::test_distinct_gaps_emit_separate_events_with_count_one`
- **Pass**: exit code 0, no skipped tests

## ADDED Requirements

### Requirement: Reasoning content extraction
Model reasoning (chain-of-thought) content — OpenAI `reasoning_content` and Anthropic `thinking` blocks — SHALL be extracted into the canonical `reasoning_text` field as one segment per message or per accumulated stream, and SHALL be inspected in full at both stages like any other text field. No `uninspected_content` event SHALL be emitted for well-formed reasoning content. Anthropic stream `signature_delta` events are protocol-only and SHALL be ignored without a gap.

#### Scenario: Non-streaming OpenAI reasoning content
- **WHEN** a non-streaming OpenAI-format response contains an assistant `message` with a non-empty `reasoning_content` field
- **THEN** the response-stage canonical `reasoning_text` contains that text as a segment and no uninspected event is emitted for it
- **ID**: T-CONTENT-NORMALIZATION-025
- **File**: tests/test_formats.py::test_openai_response_reasoning_content_extracted
- **Command**: `uv run pytest tests/test_formats.py::test_openai_response_reasoning_content_extracted`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Streamed OpenAI reasoning deltas accumulated
- **WHEN** a streamed OpenAI response delivers reasoning across `delta.reasoning_content` fragments in multiple chunks
- **THEN** the accumulated stream's canonical `reasoning_text` contains one segment with the concatenated fragments and no uninspected event is emitted for the fragments
- **ID**: T-CONTENT-NORMALIZATION-026
- **File**: tests/test_formats.py::test_openai_stream_reasoning_deltas_accumulated
- **Command**: `uv run pytest tests/test_formats.py::test_openai_stream_reasoning_deltas_accumulated`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Non-streaming Anthropic thinking blocks
- **WHEN** a non-streaming Anthropic-format response contains `thinking` content blocks
- **THEN** each block's text is a response-stage canonical `reasoning_text` segment and no uninspected event is emitted for it
- **ID**: T-CONTENT-NORMALIZATION-027
- **File**: tests/test_formats.py::test_anthropic_response_thinking_blocks_extracted
- **Command**: `uv run pytest tests/test_formats.py::test_anthropic_response_thinking_blocks_extracted`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Streamed Anthropic thinking reconstructed
- **WHEN** a streamed Anthropic response delivers a `thinking` block via `content_block_start` and `thinking_delta` events, followed by `signature_delta` events
- **THEN** the accumulated stream's canonical `reasoning_text` contains one segment with the accumulated thinking text and no uninspected event is emitted for the thinking or signature events
- **ID**: T-CONTENT-NORMALIZATION-028
- **File**: tests/test_formats.py::test_anthropic_stream_thinking_reconstructed
- **Command**: `uv run pytest tests/test_formats.py::test_anthropic_stream_thinking_reconstructed`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Malformed reasoning content is a gap
- **WHEN** an OpenAI `message.reasoning_content` or an Anthropic `thinking` block carries a non-string value
- **THEN** it is recorded in `uninspected` as an `unknown_part` gap at that location, and the rest of the message is parsed normally
- **ID**: T-CONTENT-NORMALIZATION-033
- **File**: tests/test_formats.py::test_malformed_reasoning_content_recorded_as_gap
- **Command**: `uv run pytest tests/test_formats.py::test_malformed_reasoning_content_recorded_as_gap`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Cross-format parity for reasoning content
- **WHEN** semantically identical responses containing one unit of reasoning content arrive at `/v1/chat/completions` and `/v1/messages`
- **THEN** both produce the same `reasoning_text` canonical segment
- **ID**: T-CONTENT-NORMALIZATION-029
- **File**: tests/test_parity.py::test_identical_reasoning_content_yields_identical_canonical_field
- **Command**: `uv run pytest tests/test_parity.py::test_identical_reasoning_content_yields_identical_canonical_field`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Reasoning content is rule-targetable
- **WHEN** a response-stage rule targets `reasoning_text` and the upstream response's reasoning content contains the rule's pattern (visible answer text does not)
- **THEN** the rule matches and the verdict names the canonical field `reasoning_text`
- **ID**: T-CONTENT-NORMALIZATION-030
- **File**: tests/test_engine.py::test_rule_targeting_reasoning_text_matches
- **Command**: `uv run pytest tests/test_engine.py::test_rule_targeting_reasoning_text_matches`
- **Pass**: exit code 0, no skipped tests
