# content-normalization Specification

## Purpose

Converts OpenAI-compatible and Anthropic-compatible chat requests and responses into one canonical inspection structure, so detection rules are written once and behave identically regardless of wire format.

## Requirements

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
Content the parser does not inspect (request tool definitions such as OpenAI `tools[]` and Anthropic `tools[]`, images, unknown part types, legacy `function_call` fields, malformed tool content, and stream issues) SHALL be recorded in `uninspected` with its location and type, and the gateway SHALL emit an `uninspected_content` event for it. Well-formed tool calls and tool results are NOT uninspected: they are extracted into the canonical `tool_calls` and `tool_results` fields as specified by "Tool call extraction" and "Tool result extraction"; malformed tool content (non-list `tool_calls`, non-object call entries, and stream fragments that cannot be accumulated) remains uninspected.

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

#### Scenario: Fail-closed does not block inspected tool content
- **GIVEN** `uninspected_policy` is `fail_closed`
- **WHEN** a request contains tool calls and tool results and no other uninspected content
- **THEN** the request is forwarded, because tool content is inspected rather than uninspected
- **ID**: T-CONTENT-NORMALIZATION-015
- **File**: tests/test_proxy.py::test_fail_closed_forwards_inspected_tool_content
- **Command**: `uv run pytest tests/test_proxy.py::test_fail_closed_forwards_inspected_tool_content`
- **Pass**: exit code 0, no skipped tests

### Requirement: Tool call extraction
Tool calls SHALL be extracted into the canonical `tool_calls` field as one segment per call, where a segment is the JSON encoding of the call's name and arguments (OpenAI `function.name`/`function.arguments`; Anthropic `name`/`input`) so a single pattern sees both together. Extraction SHALL apply to assistant tool calls in request history and to tool calls generated by the upstream in responses, in both formats, for non-streaming response bodies and for fully accumulated streams.

#### Scenario: OpenAI assistant tool calls in history
- **WHEN** an OpenAI request contains an assistant message with a `tool_calls` array
- **THEN** each entry becomes one `tool_calls` segment containing that call's `name` and `arguments` in a single JSON value
- **ID**: T-CONTENT-NORMALIZATION-016
- **File**: tests/test_formats.py::test_openai_request_tool_calls_extracted
- **Command**: `uv run pytest tests/test_formats.py::test_openai_request_tool_calls_extracted`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Anthropic tool_use blocks in history
- **WHEN** an Anthropic request contains an assistant message with `tool_use` blocks
- **THEN** each block becomes one `tool_calls` segment containing that block's `name` and `input` in a single JSON value
- **ID**: T-CONTENT-NORMALIZATION-017
- **File**: tests/test_formats.py::test_anthropic_request_tool_use_extracted
- **Command**: `uv run pytest tests/test_formats.py::test_anthropic_request_tool_use_extracted`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Tool calls in non-streaming responses
- **WHEN** a non-streaming upstream response in either format contains assistant tool calls
- **THEN** the response-stage canonical `tool_calls` contains one segment per call
- **ID**: T-CONTENT-NORMALIZATION-018
- **File**: tests/test_formats.py::test_response_tool_calls_extracted
- **Command**: `uv run pytest tests/test_formats.py::test_response_tool_calls_extracted`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Tool calls reconstructed from OpenAI stream deltas
- **WHEN** a streamed OpenAI response delivers a tool call across `delta.tool_calls` fragments (name in one chunk, `arguments` split across others, identified by fragment index)
- **THEN** the accumulated stream's canonical `tool_calls` contains one segment per reconstructed call and no uninspected event is emitted for the fragments
- **ID**: T-CONTENT-NORMALIZATION-019
- **File**: tests/test_formats.py::test_openai_stream_tool_calls_reconstructed
- **Command**: `uv run pytest tests/test_formats.py::test_openai_stream_tool_calls_reconstructed`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Tool calls reconstructed from Anthropic stream events
- **WHEN** a streamed Anthropic response delivers a `tool_use` block via `content_block_start` and `input_json_delta` events
- **THEN** the accumulated stream's canonical `tool_calls` contains one segment for that block with the accumulated `input` JSON
- **ID**: T-CONTENT-NORMALIZATION-020
- **File**: tests/test_formats.py::test_anthropic_stream_tool_use_reconstructed
- **Command**: `uv run pytest tests/test_formats.py::test_anthropic_stream_tool_use_reconstructed`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Cross-format parity for tool content
- **WHEN** semantically identical requests containing one tool call and one tool result arrive at `/v1/chat/completions` and `/v1/messages`
- **THEN** both produce the same `tool_calls` and `tool_results` canonical segments
- **ID**: T-CONTENT-NORMALIZATION-024
- **File**: tests/test_parity.py::test_identical_tool_content_yields_identical_canonical_fields
- **Command**: `uv run pytest tests/test_parity.py::test_identical_tool_content_yields_identical_canonical_fields`
- **Pass**: exit code 0, no skipped tests

### Requirement: Tool result extraction
Tool results SHALL be extracted into the canonical `tool_results` field as request-stage content, one segment per result's text: the string content of OpenAI `role: "tool"` messages and the text of Anthropic `tool_result` blocks (string content or text parts within block arrays). Non-string OpenAI tool-message content and image parts inside a `tool_result` SHALL remain uninspected. Every segment SHALL be inspected in full; a match SHALL be found at any position within a segment.

#### Scenario: OpenAI tool result messages
- **WHEN** a request contains messages with role `tool` whose content is a string
- **THEN** each message's content is a `tool_results` segment and no `uninspected_content` event of kind `tool_result` is emitted
- **ID**: T-CONTENT-NORMALIZATION-021
- **File**: tests/test_formats.py::test_openai_tool_result_messages_extracted
- **Command**: `uv run pytest tests/test_formats.py::test_openai_tool_result_messages_extracted`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Anthropic tool_result blocks
- **WHEN** a request contains a user message with `tool_result` blocks, including one block whose content is an array of text and image parts
- **THEN** the text parts are `tool_results` segments and the image part is recorded uninspected with kind `image`
- **ID**: T-CONTENT-NORMALIZATION-022
- **File**: tests/test_formats.py::test_anthropic_tool_result_blocks_extracted
- **Command**: `uv run pytest tests/test_formats.py::test_anthropic_tool_result_blocks_extracted`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Large tool result is fully inspected
- **WHEN** a `tool_results` segment is 1 MB and the target pattern appears near its end
- **THEN** a rule targeting `tool_results` matches it
- **ID**: T-CONTENT-NORMALIZATION-023
- **File**: tests/test_engine.py::test_large_tool_result_match_found
- **Command**: `uv run pytest tests/test_engine.py::test_large_tool_result_match_found`
- **Pass**: exit code 0, no skipped tests
