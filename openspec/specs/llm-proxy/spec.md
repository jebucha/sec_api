# llm-proxy Specification

## Purpose

Exposes OpenAI-compatible and Anthropic-compatible chat endpoints to lab clients and relays permitted traffic to self-hosted inference upstreams, enforcing routing, credential boundaries, and gateway verdicts on the wire.

## Requirements

### Requirement: Client-facing API endpoints
The gateway SHALL serve `POST /v1/chat/completions` (OpenAI-compatible), `POST /v1/messages` (Anthropic-compatible), and `GET /v1/models` over HTTP on a configurable listen address and port.

#### Scenario: OpenAI-compatible chat request is proxied
- **WHEN** an authenticated client sends a valid non-streaming `POST /v1/chat/completions` for a mapped model and no rule blocks it
- **THEN** the gateway returns the upstream's status code and response body to the client unchanged
- **ID**: T-LLM-PROXY-001
- **File**: tests/test_proxy.py::test_openai_chat_proxied_unchanged
- **Command**: `uv run pytest tests/test_proxy.py::test_openai_chat_proxied_unchanged`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Anthropic-compatible chat request is proxied
- **WHEN** an authenticated client sends a valid non-streaming `POST /v1/messages` for a mapped model and no rule blocks it
- **THEN** the gateway returns the upstream's status code and response body to the client unchanged
- **ID**: T-LLM-PROXY-002
- **File**: tests/test_proxy.py::test_anthropic_messages_proxied_unchanged
- **Command**: `uv run pytest tests/test_proxy.py::test_anthropic_messages_proxied_unchanged`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Unsupported path
- **WHEN** a client requests a path the gateway does not serve
- **THEN** the gateway responds 404 without contacting any upstream
- **ID**: T-LLM-PROXY-003
- **File**: tests/test_app.py::test_unsupported_path_returns_404_without_upstream_contact
- **Command**: `uv run pytest tests/test_app.py::test_unsupported_path_returns_404_without_upstream_contact`
- **Pass**: exit code 0, no skipped tests

### Requirement: Request size limit
The gateway SHALL reject a chat request whose body exceeds the configured `max_request_bytes` (default 8 MiB) with a provider-shaped 413 error, before parsing the body and without contacting the upstream.

#### Scenario: Oversized request body
- **WHEN** an authenticated client sends a chat request body larger than `max_request_bytes`
- **THEN** the gateway responds 413 with a provider-shaped error and contacts no upstream
- **ID**: T-LLM-PROXY-004
- **File**: tests/test_proxy.py::test_oversized_request_rejected_413
- **Command**: `uv run pytest tests/test_proxy.py::test_oversized_request_rejected_413`
- **Pass**: exit code 0, no skipped tests

### Requirement: Request correlation header
Every API response that produces security events SHALL carry an `X-Gateway-Request-Id` header equal to the `request_id` of those events. This includes allowed, blocked, errored, streamed, and 401 authentication-failure responses.

#### Scenario: Allowed response carries request id
- **WHEN** an allowed request completes
- **THEN** the response has an `X-Gateway-Request-Id` header equal to the `request_id` in that request's `request_completed` event
- **ID**: T-LLM-PROXY-005
- **File**: tests/test_proxy.py::test_allowed_response_carries_request_id_header
- **Command**: `uv run pytest tests/test_proxy.py::test_allowed_response_carries_request_id_header`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Auth failure response carries request id
- **WHEN** a request fails authentication
- **THEN** the 401 response has an `X-Gateway-Request-Id` header equal to the `request_id` in that request's `auth_failed` and `request_completed` events
- **ID**: T-LLM-PROXY-026
- **File**: tests/test_auth.py::test_401_response_carries_request_id_header
- **Command**: `uv run pytest tests/test_auth.py::test_401_response_carries_request_id_header`
- **Pass**: exit code 0, no skipped tests

### Requirement: Model-based upstream routing
The gateway SHALL select the upstream for each chat request by looking up the request's `model` value in a configured model-to-upstream map.

#### Scenario: Mapped model routes to its upstream
- **GIVEN** the config maps model `qwen3-32b` to upstream `gpu-box`
- **WHEN** a client requests model `qwen3-32b`
- **THEN** the request is forwarded to `gpu-box`'s base URL with the same API path
- **ID**: T-LLM-PROXY-006
- **File**: tests/test_routing.py::test_mapped_model_routes_to_upstream_same_path
- **Command**: `uv run pytest tests/test_routing.py::test_mapped_model_routes_to_upstream_same_path`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Unknown model fails closed
- **WHEN** a client requests a model that is not in the map
- **THEN** the gateway responds 404 with a provider-shaped "model not found" error and contacts no upstream
- **ID**: T-LLM-PROXY-007
- **File**: tests/test_routing.py::test_unknown_model_returns_404_without_upstream_contact
- **Command**: `uv run pytest tests/test_routing.py::test_unknown_model_returns_404_without_upstream_contact`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Missing model field
- **WHEN** a chat request body has no `model` field or is not valid JSON
- **THEN** the gateway responds 400 with a provider-shaped error and contacts no upstream
- **ID**: T-LLM-PROXY-008
- **File**: tests/test_routing.py::test_missing_model_or_invalid_json_returns_400
- **Command**: `uv run pytest tests/test_routing.py::test_missing_model_or_invalid_json_returns_400`
- **Pass**: exit code 0, no skipped tests

### Requirement: Model listing
`GET /v1/models` SHALL return, in OpenAI list format, exactly the model names present in the routing map.

#### Scenario: Listing configured models
- **GIVEN** the routing map contains `qwen3-32b` and `llama-3.1-8b`
- **WHEN** an authenticated client calls `GET /v1/models`
- **THEN** the response lists exactly those two model ids
- **ID**: T-LLM-PROXY-009
- **File**: tests/test_app.py::test_models_lists_exactly_routing_map
- **Command**: `uv run pytest tests/test_app.py::test_models_lists_exactly_routing_map`
- **Pass**: exit code 0, no skipped tests

### Requirement: Original request forwarding
The gateway SHALL forward the client's original request body bytes to the upstream unmodified. Canonical normalization SHALL be used for inspection only.

#### Scenario: Body is byte-identical upstream
- **WHEN** an allowed request is forwarded
- **THEN** the body received by the upstream is byte-for-byte identical to the body the client sent
- **ID**: T-LLM-PROXY-010
- **File**: tests/test_proxy.py::test_forwarded_body_byte_identical
- **Command**: `uv run pytest tests/test_proxy.py::test_forwarded_body_byte_identical`
- **Pass**: exit code 0, no skipped tests

### Requirement: Client credential stripping
The gateway SHALL remove client-supplied `Authorization` and `x-api-key` headers before forwarding any request upstream.

#### Scenario: Client token never reaches upstream
- **WHEN** a client authenticates with a gateway token and the request is forwarded
- **THEN** the upstream request contains no header carrying the client's gateway token
- **ID**: T-LLM-PROXY-011
- **File**: tests/test_routing.py::test_client_token_stripped_before_forwarding
- **Command**: `uv run pytest tests/test_routing.py::test_client_token_stripped_before_forwarding`
- **Pass**: exit code 0, no skipped tests

### Requirement: Upstream credential injection
For an upstream configured with credentials, the gateway SHALL add that credential to the forwarded request using the upstream's configured header style (`bearer` or `x-api-key`). For an upstream configured with no auth, it SHALL add no credential.

#### Scenario: Upstream requiring an API key
- **GIVEN** upstream `gpu-box` is configured with bearer auth sourced from secret `VLLM_KEY`
- **WHEN** a request is forwarded to `gpu-box`
- **THEN** the upstream request carries `Authorization: Bearer <value of VLLM_KEY>`
- **ID**: T-LLM-PROXY-012
- **File**: tests/test_routing.py::test_bearer_upstream_credential_injected
- **Command**: `uv run pytest tests/test_routing.py::test_bearer_upstream_credential_injected`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Upstream without auth
- **GIVEN** upstream `mac-mini` is configured with `auth: none`
- **WHEN** a request is forwarded to `mac-mini`
- **THEN** the upstream request carries neither `Authorization` nor `x-api-key`
- **ID**: T-LLM-PROXY-013
- **File**: tests/test_routing.py::test_no_auth_upstream_receives_no_credential
- **Command**: `uv run pytest tests/test_routing.py::test_no_auth_upstream_receives_no_credential`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Upstream credential never returned to client
- **WHEN** any response, error, or event is produced
- **THEN** it does not contain any upstream credential value
- **ID**: T-LLM-PROXY-014
- **File**: tests/test_proxy.py::test_upstream_credential_absent_from_responses_and_events
- **Command**: `uv run pytest tests/test_proxy.py::test_upstream_credential_absent_from_responses_and_events`
- **Pass**: exit code 0, no skipped tests

### Requirement: Streaming in monitor mode
When the global mode is `monitor`, `uninspected_policy` is `flag`, and a request asks for streaming, the gateway SHALL relay upstream stream chunks to the client as they arrive. It SHALL inspect the accumulated response after the stream completes. It SHALL accumulate at most `max_buffer_bytes` for inspection. If the stream exceeds that, it SHALL keep relaying, stop accumulating, inspect only the accumulated prefix, and emit an `uninspected_content` event of type `stream_overflow`. When `uninspected_policy` is `fail_closed`, a monitor-mode stream SHALL instead be buffered and evaluated before any relay (as in enforce mode), so that a stream containing uninspected content can be blocked (see content-normalization: Optional fail-closed on uninspected content).

#### Scenario: Monitor-mode stream is not delayed
- **GIVEN** mode is `monitor` and `uninspected_policy` is `flag`
- **WHEN** a client sends a streaming request
- **THEN** each upstream chunk is relayed to the client without waiting for the full response, and response-stage verdicts are emitted as events after the stream ends
- **ID**: T-LLM-PROXY-015
- **File**: tests/test_streaming.py::test_monitor_stream_relays_first_chunk_before_upstream_finishes
- **Command**: `uv run pytest tests/test_streaming.py::test_monitor_stream_relays_first_chunk_before_upstream_finishes`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Monitor-mode stream exceeds buffer cap
- **GIVEN** mode is `monitor` and `uninspected_policy` is `flag`
- **WHEN** a streamed response exceeds `max_buffer_bytes`
- **THEN** the client receives the complete stream and an `uninspected_content` event of type `stream_overflow` is recorded
- **ID**: T-LLM-PROXY-016
- **File**: tests/test_streaming.py::test_monitor_stream_overflow_relays_all_and_emits_event
- **Command**: `uv run pytest tests/test_streaming.py::test_monitor_stream_overflow_relays_all_and_emits_event`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Monitor-mode stream under fail_closed is buffered and blocked
- **GIVEN** mode is `monitor` and `uninspected_policy` is `fail_closed`
- **WHEN** a streamed response contains uninspected content (e.g. a tool-call delta)
- **THEN** the client receives a block response and none of the upstream's generated content
- **ID**: T-LLM-PROXY-027
- **File**: tests/test_streaming.py::test_monitor_fail_closed_stream_uninspected_is_blocked
- **Command**: `uv run pytest tests/test_streaming.py::test_monitor_fail_closed_stream_uninspected_is_blocked`
- **Pass**: exit code 0, no skipped tests

### Requirement: Streaming in enforce mode
When the global mode is `enforce` and a request asks for streaming, the gateway SHALL buffer the complete upstream stream, up to `max_buffer_bytes`, and run response-stage detection. It SHALL then either replay the buffered stream to the client or return a block response. A stream that exceeds `max_buffer_bytes` SHALL be treated as a detection error (see detection-engine: Detection errors fail per mode) and blocked.

#### Scenario: Enforce-mode stream is clean
- **GIVEN** mode is `enforce`
- **WHEN** a streamed response triggers no blocking rule
- **THEN** the client receives the same stream events the upstream produced, delivered after the upstream stream completes
- **ID**: T-LLM-PROXY-017
- **File**: tests/test_streaming.py::test_enforce_clean_stream_replayed_byte_identical
- **Command**: `uv run pytest tests/test_streaming.py::test_enforce_clean_stream_replayed_byte_identical`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Enforce-mode stream is blocked
- **GIVEN** mode is `enforce`
- **WHEN** a streamed response triggers a rule with action `block`
- **THEN** the client receives a block response and none of the upstream's generated content
- **ID**: T-LLM-PROXY-018
- **File**: tests/test_streaming.py::test_enforce_blocked_stream_returns_block_without_content
- **Command**: `uv run pytest tests/test_streaming.py::test_enforce_blocked_stream_returns_block_without_content`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Enforce-mode stream exceeds buffer cap
- **GIVEN** mode is `enforce`
- **WHEN** a streamed response exceeds `max_buffer_bytes`
- **THEN** the client receives a block response with none of the upstream's generated content, and a `rule_error` event records the overflow
- **ID**: T-LLM-PROXY-019
- **File**: tests/test_streaming.py::test_enforce_stream_overflow_blocked
- **Command**: `uv run pytest tests/test_streaming.py::test_enforce_stream_overflow_blocked`
- **Pass**: exit code 0, no skipped tests

### Requirement: Provider-shaped block responses
When a request or response is blocked, the gateway SHALL respond 403 with an error body in the wire format of the client's endpoint (OpenAI or Anthropic error shape) and an `X-Gateway-Event-Id` header. That header identifies the `verdict` event, as specified in security-events: Verdict events.

#### Scenario: Blocked OpenAI-format request
- **WHEN** a request to `/v1/chat/completions` is blocked
- **THEN** the response is 403 with an OpenAI-shaped error object of type `gateway_blocked` and an `X-Gateway-Event-Id` header
- **ID**: T-LLM-PROXY-020
- **File**: tests/test_proxy.py::test_openai_block_response_shape
- **Command**: `uv run pytest tests/test_proxy.py::test_openai_block_response_shape`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Blocked Anthropic-format request
- **WHEN** a request to `/v1/messages` is blocked
- **THEN** the response is 403 with an Anthropic-shaped error object of type `gateway_blocked` and an `X-Gateway-Event-Id` header
- **ID**: T-LLM-PROXY-021
- **File**: tests/test_proxy.py::test_anthropic_block_response_shape
- **Command**: `uv run pytest tests/test_proxy.py::test_anthropic_block_response_shape`
- **Pass**: exit code 0, no skipped tests

### Requirement: Opaque block details
Block responses SHALL NOT disclose rule ids, rule names, patterns, or matched text unless `debug_verdicts` is enabled in the configuration.

#### Scenario: Default opaque block
- **GIVEN** `debug_verdicts` is false
- **WHEN** a request is blocked
- **THEN** the error message contains only a generic policy message and the event id reference
- **ID**: T-LLM-PROXY-022
- **File**: tests/test_proxy.py::test_block_message_opaque_by_default
- **Command**: `uv run pytest tests/test_proxy.py::test_block_message_opaque_by_default`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Debug verdicts enabled
- **GIVEN** `debug_verdicts` is true
- **WHEN** a request is blocked
- **THEN** the error body additionally includes the ids of the rules that matched
- **ID**: T-LLM-PROXY-023
- **File**: tests/test_proxy.py::test_block_message_includes_rule_ids_with_debug_verdicts
- **Command**: `uv run pytest tests/test_proxy.py::test_block_message_includes_rule_ids_with_debug_verdicts`
- **Pass**: exit code 0, no skipped tests

### Requirement: Upstream failure handling
The gateway SHALL return a provider-shaped 502 error when an upstream is unreachable, and a 504 error when it exceeds the configured timeout.

#### Scenario: Upstream down
- **WHEN** the selected upstream refuses the connection
- **THEN** the client receives a provider-shaped 502 error and an event is recorded
- **ID**: T-LLM-PROXY-024
- **File**: tests/test_proxy.py::test_upstream_connection_refused_returns_502
- **Command**: `uv run pytest tests/test_proxy.py::test_upstream_connection_refused_returns_502`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Upstream timeout
- **WHEN** the selected upstream does not respond within the configured timeout
- **THEN** the client receives a provider-shaped 504 error
- **ID**: T-LLM-PROXY-025
- **File**: tests/test_proxy.py::test_upstream_timeout_returns_504
- **Command**: `uv run pytest tests/test_proxy.py::test_upstream_timeout_returns_504`
- **Pass**: exit code 0, no skipped tests
