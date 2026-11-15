# Proposal

## Why

Agent clients (OpenCode and similar) send most of their traffic as tool calls and tool results — file contents, command output, web fetches — and the gateway currently records all of it as uninspected. Detection rules therefore never see the content that actually carries prompt-injection and secret-exfiltration payloads in agentic flows, which defeats the gateway's purpose for its primary client.

## What Changes

- Canonical parsers extract tool calls into `tool_calls` (request and response stages) and tool results into `tool_results` (request stage) (OpenAI and Anthropic formats) instead of dropping them into `uninspected` gaps.
- Tool call segment shape: one segment per call, the JSON encoding of the call's name and arguments, so a single pattern sees both together. Tool result segments are the result's text content.
- Streamed responses are covered: tool calls are reconstructed from SSE deltas (OpenAI `delta.tool_calls` fragments per index; Anthropic `tool_use` block + `input_json_delta` fragments) before inspection.
- `uninspected_content` events of kinds `tool_call` and `tool_result` stop being emitted for well-formed tool content. Tool definitions (`tools[]`), images, unknown part types, stream issues, and malformed tool content (non-list `tool_calls`, non-object call entries, unparseable stream fragments) remain uninspected, as do legacy `function_call` fields.
- Starter rule packs' prompt-injection, secrets, and PII rules widen their `target` to include `tool_results` (no new rules) so the new channel is covered out of the box.
- Behavior change (intended, not breaking): rules whose `target` already includes `tool_calls` or `tool_results` — schema-valid since v1 — start matching real content, and requests that previously carried only uninspected tool content can now produce verdicts.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `content-normalization`: tool calls become inspected canonical content at both stages and both formats; tool results become inspected canonical content at the request stage; both include accumulated streams. The uninspected set shrinks to tool definitions, images, unknown part types, legacy `function_call`, and stream gaps.
- `detection-rules`: starter rule packs must include rules targeting `tool_results` so the tool channel is covered by default.

## Impact

- Code: `src/llmgw/formats/openai.py` and `src/llmgw/formats/anthropic.py` (request/response parsers and stream extraction), `src/llmgw/formats/canonical.py` (docstring; field shapes), `src/llmgw/proxy.py` (stream inspection path), starter packs under `rules/`. The rule schema, loader, and engine are unchanged — `tool_calls`/`tool_results` are already valid `target` values and the engine already evaluates them.
- Events: `uninspected_content` volume drops (no more `tool_call`/`tool_result` kinds); `verdict` events can now carry matches with `field` `tool_calls`/`tool_results`.
- Tests: `tests/test_formats.py`, `tests/test_parity.py`, `tests/test_engine.py`, `tests/test_proxy.py`, `tests/test_starter_packs.py`, `tests/test_streaming.py`.
- Operations: no config or API changes. Deployments that rely on `uninspected_policy: fail_closed` for agent traffic will no longer see tool content trigger it (tool definitions/images still do).
