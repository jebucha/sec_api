# content-normalization Specification

## Purpose

Converts OpenAI-compatible and Anthropic-compatible chat requests and responses into one canonical inspection structure, so detection rules are written once and behave identically regardless of wire format.

## Requirements

### Requirement: Canonical inspection fields
The gateway SHALL normalize every chat request and response into canonical fields: `system`, `user_text`, `assistant_text`, `reasoning_text`, `tool_calls`, `tool_results`, `tool_definitions`, and `uninspected`. Rules SHALL target canonical field names, never wire-format paths.

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
Content the parser does not inspect (images, unknown part types, legacy `function_call` fields, malformed tool content, and stream issues) SHALL be recorded in `uninspected` with its location and type, and the gateway SHALL emit an `uninspected_content` event for it. Well-formed tool calls, tool results, and tool definitions are NOT uninspected: they are extracted into the canonical `tool_calls`, `tool_results`, and `tool_definitions` fields as specified by "Tool call extraction", "Tool result extraction", and "Tool definition extraction"; malformed tool content (non-list `tool_calls`, non-object call entries, and stream fragments that cannot be accumulated) and malformed tool definitions (non-object tool entries, and OpenAI `tools[]` objects that are not `function`-type entries with a `function` object) remain uninspected. For each stage, the gateway SHALL emit at most one `uninspected_content` event per unique combination of kind, location, and detail, and SHALL include a `count` field in each event giving the number of gaps aggregated into it.

#### Scenario: Tool-use block in v1
- **WHEN** a request contains an Anthropic `tool_use` or `tool_result` block or an OpenAI `tool_calls` entry or `role: "tool"` message
- **THEN** its content appears in the canonical `tool_calls` or `tool_results` field and no `uninspected_content` event of kind `tool_call` or `tool_result` is emitted
- **ID**: T-CONTENT-NORMALIZATION-009
- **File**: tests/test_formats.py::test_tool_blocks_no_longer_emit_uninspected_events
- **Command**: `uv run pytest tests/test_formats.py::test_tool_blocks_no_longer_emit_uninspected_events`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Tool definitions in v1
- **WHEN** a request contains a non-empty `tools` array
- **THEN** the definitions appear in the canonical `tool_definitions` field and no `uninspected_content` event of kind `tool_definitions` is emitted
- **ID**: T-CONTENT-NORMALIZATION-010
- **File**: tests/test_formats.py::test_tool_definitions_extracted_without_uninspected_event
- **Command**: `uv run pytest tests/test_formats.py::test_tool_definitions_extracted_without_uninspected_event`
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
- **WHEN** a request contains tool calls, tool results, and a non-empty `tools` array and no other uninspected content
- **THEN** the request is forwarded, because tool content and tool definitions are inspected rather than uninspected
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

### Requirement: Reasoning content extraction
Model reasoning (chain-of-thought) content — OpenAI `reasoning_content`, the OpenAI-compatible `reasoning` field, and Anthropic `thinking` blocks — SHALL be extracted into the canonical `reasoning_text` field as one segment per message or per accumulated stream, and SHALL be inspected in full at both stages like any other text field. The `reasoning` field is an alias of `reasoning_content`: when both are present on the same message or delta, the `reasoning_content` value SHALL be used and the alias value ignored, so an upstream that mirrors its reasoning into both fields produces exactly one segment. No `uninspected_content` event SHALL be emitted for well-formed reasoning content in either field name. A non-string value in either field is malformed reasoning content. Anthropic stream `signature_delta` events are protocol-only and SHALL be ignored without a gap.

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

### Requirement: Tool definition extraction
Tool definitions SHALL be extracted into the canonical `tool_definitions` field as request-stage content, one segment per tool definition. A segment is the deterministic JSON encoding (sorted keys) of the normalized definition — `name`, `description`, and parameter schema — so a single pattern sees the tool's identity, documented behavior, and accepted parameters together. OpenAI `tools[].function` (with `parameters`), legacy top-level `functions[]`, and Anthropic `tools[]` (with `input_schema`) SHALL normalize to the same segment shape, so semantically identical definitions produce identical segments. Well-formed tool definitions SHALL NOT be uninspected; every segment SHALL be inspected in full, and a match SHALL be found at any position within a segment.

#### Scenario: OpenAI tool definitions extracted
- **WHEN** an OpenAI request contains a non-empty `tools` array of `function`-type entries
- **THEN** each entry becomes one `tool_definitions` segment containing that tool's `name`, `description`, and parameter schema in a single JSON value, and no uninspected event is emitted
- **ID**: T-CONTENT-NORMALIZATION-037
- **File**: tests/test_formats.py::test_openai_tool_definitions_extracted
- **Command**: `uv run pytest tests/test_formats.py::test_openai_tool_definitions_extracted`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Anthropic tool definitions extracted
- **WHEN** an Anthropic request contains a non-empty `tools` array
- **THEN** each entry becomes one `tool_definitions` segment containing that tool's `name`, `description`, and `input_schema` in a single JSON value, and no uninspected event is emitted
- **ID**: T-CONTENT-NORMALIZATION-038
- **File**: tests/test_formats.py::test_anthropic_tool_definitions_extracted
- **Command**: `uv run pytest tests/test_formats.py::test_anthropic_tool_definitions_extracted`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Legacy OpenAI function definitions extracted
- **WHEN** an OpenAI request uses the legacy top-level `functions` array instead of `tools`
- **THEN** each entry becomes one `tool_definitions` segment and no `uninspected_content` event is emitted for the definitions
- **ID**: T-CONTENT-NORMALIZATION-039
- **File**: tests/test_formats.py::test_legacy_openai_functions_extracted
- **Command**: `uv run pytest tests/test_formats.py::test_legacy_openai_functions_extracted`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Cross-format parity for tool definitions
- **WHEN** semantically identical tool definitions (same names, descriptions, and JSON-Schema parameter shapes) arrive at `/v1/chat/completions` and `/v1/messages`
- **THEN** both produce the same `tool_definitions` canonical segments
- **ID**: T-CONTENT-NORMALIZATION-040
- **File**: tests/test_parity.py::test_identical_tool_definitions_yield_identical_canonical_field
- **Command**: `uv run pytest tests/test_parity.py::test_identical_tool_definitions_yield_identical_canonical_field`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Tool definitions are rule-targetable
- **WHEN** a request-stage rule targets `tool_definitions` and a tool's description or parameter schema contains the rule's pattern (no other targeted field does)
- **THEN** the rule matches at the request stage and the verdict names the canonical field `tool_definitions`
- **ID**: T-CONTENT-NORMALIZATION-041
- **File**: tests/test_engine.py::test_rule_targeting_tool_definitions_matches
- **Command**: `uv run pytest tests/test_engine.py::test_rule_targeting_tool_definitions_matches`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Malformed tool definition is a gap
- **WHEN** a request's `tools` array contains a non-object entry (e.g. a string), or an OpenAI `tools[]` object that is not a `function`-type entry with a `function` object, alongside well-formed entries
- **THEN** the malformed entry is recorded in `uninspected` with kind `tool_definitions` at its position, the well-formed entries are still extracted into `tool_definitions`, and parsing of the rest of the request is unaffected
- **ID**: T-CONTENT-NORMALIZATION-042
- **File**: tests/test_formats.py::test_malformed_tool_definition_recorded_as_gap
- **Command**: `uv run pytest tests/test_formats.py::test_malformed_tool_definition_recorded_as_gap`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Malformed reasoning content is a gap
- **WHEN** an OpenAI `message.reasoning_content`, a streamed or non-streaming OpenAI `reasoning` field, or an Anthropic `thinking` block carries a non-string value
- **THEN** it is recorded in `uninspected` as an `unknown_part` gap at that location, and the rest of the message is parsed normally
- **ID**: T-CONTENT-NORMALIZATION-033
- **File**: tests/test_formats.py::test_malformed_reasoning_content_recorded_as_gap
- **Command**: `uv run pytest tests/test_formats.py::test_malformed_reasoning_content_recorded_as_gap`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Non-streaming OpenAI reasoning alias
- **WHEN** a non-streaming OpenAI-format response contains an assistant `message` with a non-empty `reasoning` field and no `reasoning_content` field
- **THEN** the response-stage canonical `reasoning_text` contains that text as a segment and no uninspected event is emitted for it
- **ID**: T-CONTENT-NORMALIZATION-034
- **File**: tests/test_formats.py::test_openai_response_reasoning_alias_extracted
- **Command**: `uv run pytest tests/test_formats.py::test_openai_response_reasoning_alias_extracted`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Streamed OpenAI reasoning alias accumulated
- **WHEN** a streamed OpenAI response delivers reasoning across `delta.reasoning` fragments in multiple chunks and no `reasoning_content` fragments
- **THEN** the accumulated stream's canonical `reasoning_text` contains one segment with the concatenated fragments and no uninspected event is emitted for the fragments
- **ID**: T-CONTENT-NORMALIZATION-035
- **File**: tests/test_formats.py::test_openai_stream_reasoning_alias_accumulated
- **Command**: `uv run pytest tests/test_formats.py::test_openai_stream_reasoning_alias_accumulated`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Mirrored reasoning fields are not duplicated
- **WHEN** a streamed OpenAI response (or a non-streaming message) carries both `reasoning_content` and `reasoning` on the same delta (or message), mirroring the same text
- **THEN** the canonical `reasoning_text` contains exactly one segment with that content (the `reasoning_content` value), no duplicate segment is produced, and no uninspected event is emitted
- **ID**: T-CONTENT-NORMALIZATION-036
- **File**: tests/test_formats.py::test_mirrored_reasoning_fields_not_duplicated
- **Command**: `uv run pytest tests/test_formats.py::test_mirrored_reasoning_fields_not_duplicated`
- **Pass**: exit code 0, no skipped tests
