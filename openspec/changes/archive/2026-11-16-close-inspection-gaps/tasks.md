# Tasks

## 1. `reasoning` alias — non-streaming (design D1, D5)

- [x] 1.1 OpenAI non-streaming response parser (`formats/openai.py` `parse_response`): extract `message.reasoning` as an alias of `reasoning_content` with per-message fallback (prefer `reasoning_content` when both are present); a non-string value in either field is a malformed-reasoning `unknown_part` gap at that location. Verify T-CONTENT-NORMALIZATION-034 and the extended T-CONTENT-NORMALIZATION-033 in `tests/test_formats.py`

## 2. `reasoning` alias — streaming (design D1)

- [x] 2.1 OpenAI stream parser (`formats/openai.py` `extract_stream_text`): accumulate `delta.reasoning` into the single reasoning segment with per-chunk fallback (`reasoning_content` preferred when both are on the same delta), and add `reasoning` to the known delta fields so the non-text catch-all no longer fires for it; a non-string `delta.reasoning` or `reasoning_content` value is a malformed-reasoning `unknown_part` gap at that location (design D1), so the known-fields addition does not let malformed alias values pass silently. Verify T-CONTENT-NORMALIZATION-035 and T-CONTENT-NORMALIZATION-036 (mirrored fields yield exactly one segment, no event), the streamed non-string case of T-CONTENT-NORMALIZATION-033, and that T-CONTENT-NORMALIZATION-026 still passes in `tests/test_formats.py`

## 3. Canonical `tool_definitions` field (design D3, D4)

- [x] 3.1 Add the `tool_definitions` field to the `Canonical` dataclass and `CANONICAL_FIELDS` in `src/llmgw/formats/canonical.py`, plus the shared definition-segment builder: normalized `{"description", "input_schema", "name"}` serialized with `sort_keys=True` (missing `description`/schema → `null`), and update the module docstring's gap-kinds list. Verify with unit tests in `tests/test_canonical.py` covering deterministic serialization, missing fields, and format-independence, and update the hard-coded `CANONICAL_FIELDS` set in `test_canonical_fields_match_the_contract` to include `tool_definitions`
- [x] 3.2 OpenAI request parser (`formats/openai.py` `parse_request`): extract `tools[]` entries (`function.name`/`description`/`parameters`) and legacy top-level `functions[]` entries into one `tool_definitions` segment each; non-object entries and non-`function`-type `tools[]` entries become positional `tool_definitions` gaps while well-formed siblings still extract; remove the "not inspected in v1" gaps. Verify T-CONTENT-NORMALIZATION-037, -039, and -042, and replace the superseded T-CONTENT-NORMALIZATION-010 test (`test_tool_definitions_emit_uninspected_content_event` → `test_tool_definitions_extracted_without_uninspected_event`) in `tests/test_formats.py`
- [x] 3.3 Anthropic request parser (`formats/anthropic.py` `parse_request`): extract `tools[]` entries (`name`/`description`/`input_schema`) into one segment each; non-object entries become positional `tool_definitions` gaps; remove the "not inspected in v1" gap. Verify T-CONTENT-NORMALIZATION-038 in `tests/test_formats.py`
- [x] 3.4 Extend `tests/test_parity.py` so semantically identical tool definitions (same names, descriptions, JSON-Schema shapes) through both formats yield identical `tool_definitions` segments. Verify T-CONTENT-NORMALIZATION-040

## 4. Rule targeting

- [x] 4.1 Add `tool_definitions` to the `target` enum in `rules/schema.json` and `src/llmgw/rules/loader.py` (no engine change — `engine.py` resolves targets via `getattr`). Verify in `tests/test_rules_loader.py` that a rule targeting `tool_definitions` loads and an unknown target is still rejected
- [x] 4.2 Add the engine test: a request-stage rule targeting `tool_definitions` matches a pattern inside a tool description/parameter schema and the verdict names the field `tool_definitions`. Verify T-CONTENT-NORMALIZATION-041 in `tests/test_engine.py`

## 5. Starter packs and docs (design D2)

- [x] 5.1 Widen `target` from `["user_text", "tool_results"]` to `["user_text", "tool_results", "tool_definitions"]` and bump `version` (docs/rules.md: "bump on change") on the request-stage secrets rules (`SE-001`–`SE-004`) in `rules/secrets.jsonl` (no pattern or self-test changes). Verify T-DETECTION-RULES-019 (starter packs load with zero rejections) and add T-DETECTION-RULES-024 (secret pattern in a tool definition matches with a masked excerpt) in `tests/test_starter_packs.py`
- [x] 5.2 Update `docs/rules.md`: `tool_definitions` is a live target, with the D3 segment shape documented and one example rule. Verify the doc-extraction test in `tests/test_rules_doc.py` still passes

## 6. Gap and fail-closed behavior

- [x] 6.1 Verify `uninspected_policy: fail_closed` forwards a request containing a `tools` array plus tool calls/results and no other uninspected content (tool definitions are now inspected). Verify the updated T-CONTENT-NORMALIZATION-015 in `tests/test_proxy.py`
- [x] 6.2 Update `tests/test_proxy.py::test_distinct_gaps_emit_separate_events_with_count_one` (T-CONTENT-NORMALIZATION-032): the request's `tools: [{"type": "function"}]` entry (no `function` object) now yields a `tool_definitions` gap at positional location `tools[0]` instead of `tools` (design D4); update the expected gap tuple

## 7. End-to-end verification

- [x] 7.1 Run the full suite and linter. Verify `uv run pytest` and `uv run ruff check` both exit 0
- [x] 7.2 Live acceptance against the gateway (container, monitor mode): send one OpenAI request containing a non-empty `tools` array, then a streaming request against the mirrored-reasoning upstream. Verify the events file shows no `uninspected_content` events at all for that traffic shape (no `tool_definitions` request gap, no `unknown_part` stream gap) and that reasoning content is still rule-targetable at the response stage

## Workflow follow-up

- After implementation is complete and verified: run the audit workflow, then `openspec-sync-specs` + `openspec-archive-change` for this change (the `content-normalization` and `detection-rules` deltas update the main specs under `openspec/specs/`).
