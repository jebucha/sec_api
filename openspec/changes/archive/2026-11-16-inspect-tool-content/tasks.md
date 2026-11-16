# Tasks

## 1. Shared tool-content segment helpers

- [x] 1.1 Add the shared tool-call segment builder (design D1) in `src/llmgw/formats/canonical.py`: one JSON object per call, `{"input": <object>, "name": <str>}` serialized with `sort_keys=True`; lenient parse of OpenAI's `arguments` string to the `input` object with the raw string as fallback. Verify with unit tests in `tests/test_formats.py` covering name+object, unparseable-arguments fallback, and deterministic serialization
- [x] 1.2 Update the `formats/canonical.py` module docstring: `tool_calls`/`tool_results` are populated (no longer "stay empty in v1"), and the deferred-tool-inspection note is replaced by the gap-kinds-that-remain list. Verify `uv run pytest` still passes and the docstring matches behavior

## 2. Request-stage extraction

- [x] 2.1 OpenAI request parser (`formats/openai.py` `parse_request`): each `tool_calls` entry on an assistant message becomes one `tool_calls` segment; each `role: "tool"` message's string content becomes a `tool_results` segment (non-string content stays a `tool_result` gap). Remove the now-obsolete gaps, keep gaps for non-list `tool_calls`, non-object entries, and legacy `function_call`. Verify T-CONTENT-NORMALIZATION-016, -021, -009 (OpenAI side) in `tests/test_formats.py`
- [x] 2.2 Anthropic request parser (`formats/anthropic.py` `parse_request`): each `tool_use` block becomes one `tool_calls` segment; each `tool_result` block's string content becomes a `tool_results` segment and each text part of an array content becomes a segment, with image parts still recorded uninspected (kind `image`). Verify T-CONTENT-NORMALIZATION-017, -022, -009 (Anthropic side) in `tests/test_formats.py`
- [x] 2.3 Extend `tests/test_parity.py` so identical tool call + tool result payloads through both formats yield identical `tool_calls`/`tool_results` segments. Verify T-CONTENT-NORMALIZATION-024

## 3. Response-stage extraction

- [x] 3.1 OpenAI non-streaming response parser: `choices[].message.tool_calls` entries become `tool_calls` segments. Verify T-CONTENT-NORMALIZATION-018 (OpenAI side) in `tests/test_formats.py`
- [x] 3.2 Anthropic non-streaming response parser: `content[]` `tool_use` blocks become `tool_calls` segments. Verify T-CONTENT-NORMALIZATION-018 (Anthropic side) in `tests/test_formats.py`

## 4. Stream reconstruction

- [x] 4.1 Change both formats' stream entry point to return a full `Canonical` (assistant text + reconstructed tool calls + gaps) instead of `(text, gaps)`, and update the two `proxy.py` call sites (monitor tee, enforce replay) plus the existing direct test call sites that unpack the old two-tuple (currently nine in `tests/test_formats.py`) to build the response canonical from it. Verify the existing suite in `tests/test_streaming.py` and `tests/test_formats.py` passes with the new signature
- [x] 4.2 OpenAI stream reconstruction: accumulate `choices[].delta.tool_calls[]` fragments by `index` (name from the first fragment, `arguments` string fragments concatenated) and yield one D1 segment per complete call at stream end. Verify T-CONTENT-NORMALIZATION-019 with a fixture stream that splits one tool call across chunks
- [x] 4.3 Anthropic stream reconstruction: open a slot on `content_block_start` (`type: "tool_use"`, capture `name`, key by block index), concatenate `input_json_delta.partial_json` fragments, and yield one D1 segment on `content_block_stop` (parsed `input`, raw-string fallback). Verify T-CONTENT-NORMALIZATION-020 with a fixture stream
- [x] 4.4 Record uninspected gaps (kind `tool_call`, detail without raw content) for stream fragments that cannot be accumulated (missing index, non-object entries, nothing reconstructable). Verify with `tests/test_formats.py` cases and the redaction guard from the archived change's task 2.2

## 5. Gap and fail-closed behavior

- [x] 5.1 Remove well-formed tool content from the uninspected set end to end: no `uninspected_content` events of kind `tool_call`/`tool_result` for healthy traffic in either format, either stage. Update or replace the superseded test `tests/test_formats.py::test_tool_blocks_emit_uninspected_content_event`, and update `tests/test_formats.py::test_tool_call_gap_locations_are_positional` so its positional-gap assertions target malformed tool content (non-list `tool_calls`, non-object entries) instead of well-formed calls. Verify T-CONTENT-NORMALIZATION-009 and that `test_tool_definitions_emit_uninspected_content_event` (T-010) still passes unchanged
- [x] 5.2 Verify `uninspected_policy: fail_closed` forwards a request whose only gap-carrying content is tool calls/results (fail-closed still blocks on images/tool definitions). Verify T-CONTENT-NORMALIZATION-015 in `tests/test_proxy.py`

## 6. Detection against the new fields

- [x] 6.1 Add the engine test: a 1 MB `tool_results` segment with the pattern near its end matches a `tool_results`-targeting rule. Verify T-CONTENT-NORMALIZATION-023 in `tests/test_engine.py`
- [x] 6.2 Add a verdict-event assertion that a `tool_results` match appears in the `verdict` event with `field: "tool_results"` and a bounded excerpt. Verify in `tests/test_proxy.py` against `httpx.MockTransport`

## 7. Starter rule packs and docs

- [x] 7.1 Widen `target` from `["user_text"]` to `["user_text", "tool_results"]` and bump `version` on each widened rule (docs/rules.md: "bump on change") on the request-stage prompt-injection (`PI-*`), secrets, and PII rules in `rules/` per design D6 (no pattern or self-test changes). Verify the starter packs still load with zero rejections (T-DETECTION-RULES-019) and add T-DETECTION-RULES-021 (injection marker in a `role: "tool"` message matches at the request stage) and T-DETECTION-RULES-022 (secret/PII pattern in a tool result matches with a masked excerpt) to `tests/test_starter_packs.py`
- [x] 7.2 Update `docs/rules.md`: `tool_calls`/`tool_results` targets are now live, with the D1 segment shape documented and one example rule per field. Verify every example rule in the doc loads via the existing doc-extraction test

## 8. End-to-end verification

- [x] 8.1 Run the full suite and linter. Verify `uv run pytest` and `uv run ruff check` both exit 0
- [x] 8.2 Live acceptance against the gateway (container, monitor mode): send one OpenAI request containing an assistant `tool_calls` entry, a `role: "tool"` message, and a `tools[]` array. Verify the events file shows no `uninspected_content` events of kind `tool_call`/`tool_result`, exactly one of kind `tool_definitions`, and a `verdict` event when a starter rule matches the tool result; confirm the same via `scripts/lab_monitor.py`

## Workflow follow-up

- After implementation is complete and verified: run the audit workflow, then `openspec-sync-specs` + `openspec-archive-change` for this change (the `content-normalization` and `detection-rules` MODIFIED deltas update the main specs under `openspec/specs/`).
