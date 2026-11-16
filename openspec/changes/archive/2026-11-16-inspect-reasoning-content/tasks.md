# Tasks

## 1. Canonical field

- [x] 1.1 Add `reasoning_text: tuple[str, ...] = ()` to `Canonical` and `"reasoning_text"` to `CANONICAL_FIELDS` in `src/llmgw/formats/canonical.py`; extend the module docstring's field/gap-kind list; update the hard-coded `CANONICAL_FIELDS` set in `tests/test_canonical.py::test_canonical_fields_match_the_contract`. Verify: `uv run pytest` stays green (field is empty by default, so all existing constructors are unaffected)

## 2. OpenAI extraction

- [x] 2.1 `parse_request` and `parse_response` (`src/llmgw/formats/openai.py`): extract string `message.reasoning_content` into `reasoning_text` (one segment per message); a non-string `reasoning_content` is an `unknown_part` gap at the message location. Verify: `uv run pytest tests/test_formats.py -k reasoning` (new tests from task 2.3)
- [x] 2.2 `extract_stream_text`: accumulate string `delta.reasoning_content` fragments and add `"reasoning_content"` to the delta whitelist so it no longer counts as a non-text delta field; finalize as one `reasoning_text` segment at stream end. Verify: `uv run pytest tests/test_formats.py -k reasoning`
- [x] 2.3 Add tests: T-CONTENT-NORMALIZATION-025 `tests/test_formats.py::test_openai_response_reasoning_content_extracted`, T-026 `tests/test_formats.py::test_openai_stream_reasoning_deltas_accumulated` (assert no uninspected gaps for the fragments), plus T-033 `tests/test_formats.py::test_malformed_reasoning_content_recorded_as_gap` (non-string `reasoning_content` or non-string Anthropic `thinking` text → `unknown_part` gap at the message/block location). Verify: `uv run pytest tests/test_formats.py -k reasoning`

## 3. Anthropic extraction

- [x] 3.1 `_route_block`/`_parse_blocks` (`src/llmgw/formats/anthropic.py`): route `type: "thinking"` blocks (string `thinking` text) into a reasoning bucket at request history and response stages; non-string text is a gap (T-033, task 2.3). Update `_gap_kind_for_block` docs if it references thinking. Verify: `uv run pytest tests/test_formats.py -k thinking` (new tests from task 3.3)
- [x] 3.2 `extract_stream_text`: `content_block_start` `type: "thinking"` opens a slot by block index; `thinking_delta` accumulates `thinking` fragments; `content_block_stop` closes the slot into one segment; `signature_delta` is ignored as protocol-only; a `thinking_delta` with no open slot is a gap. Verify: `uv run pytest tests/test_formats.py -k thinking`
- [x] 3.3 Add tests: T-CONTENT-NORMALIZATION-027 `tests/test_formats.py::test_anthropic_response_thinking_blocks_extracted` and T-028 `tests/test_formats.py::test_anthropic_stream_thinking_reconstructed` (assert no gaps for thinking and signature events). Verify: `uv run pytest tests/test_formats.py -k thinking`

## 4. Rule targeting

- [x] 4.1 Add `"reasoning_text"` to the `target` enum in `src/llmgw/rules/loader.py` and in the published schema `rules/schema.json` (a loader test keeps the two equal), and to `TEXT_FIELDS` in `src/llmgw/replay/corpus.py`. Verify: a rule with `target: ["reasoning_text"]` loads; `uv run pytest tests/test_rules_loader.py tests/test_engine.py`
- [x] 4.2 Add T-CONTENT-NORMALIZATION-030 `tests/test_engine.py::test_rule_targeting_reasoning_text_matches` (pattern present only in reasoning content; verdict match names field `reasoning_text`). Verify: `uv run pytest tests/test_engine.py::test_rule_targeting_reasoning_text_matches`
- [x] 4.3 Add T-CONTENT-NORMALIZATION-029 `tests/test_parity.py::test_identical_reasoning_content_yields_identical_canonical_field` (same reasoning text via both formats yields the same `reasoning_text` segment). Verify: `uv run pytest tests/test_parity.py`

## 5. Starter pack

- [x] 5.1 Add `"reasoning_text"` to PL-001's `target` in `rules/prompt-leak.jsonl` (bump `version`); keep inline tests passing. Verify: `uv run pytest tests/test_starter_packs.py`
- [x] 5.2 Add T-DETECTION-RULES-023 `tests/test_starter_packs.py::test_canary_in_reasoning_content_matches_prompt_leak_rule` (canary only in reasoning content → response-stage match with field `reasoning_text`). Verify: `uv run pytest tests/test_starter_packs.py::test_canary_in_reasoning_content_matches_prompt_leak_rule`

## 6. Event dedup

- [x] 6.1 Add `count: int` to `uninspected_content_event` (`src/llmgw/events.py`); change `_emit_gaps` (`src/llmgw/proxy.py`) to group gaps per stage by (kind, location, detail) and emit one event per group with its count. Verify: `uv run pytest tests/test_proxy.py -k gap` (new tests from task 6.2)
- [x] 6.2 Add T-CONTENT-NORMALIZATION-031 `tests/test_proxy.py::test_duplicate_gaps_collapse_into_one_event_with_count` (20 identical gaps → one event, `count: 20`) and T-032 `tests/test_proxy.py::test_distinct_gaps_emit_separate_events_with_count_one`. Verify: `uv run pytest tests/test_proxy.py -k gap`
- [x] 6.3 Update or replace existing tests that assert one `uninspected_content` event per gap (e.g. `tests/test_proxy.py` gap-count assertions, `tests/test_streaming.py` overflow/error events) so they assert the deduplicated events plus `count`. Verify: `uv run pytest` full suite green

## 7. Docs

- [x] 7.1 Update `docs/rules.md`: add `reasoning_text` to the field reference and note that reasoning/thinking content is inspected (both formats, both stages); note `uninspected_content` now carries `count` in `docs/` event documentation where events are described. Verify: `grep -n reasoning_text docs/rules.md` and a read-through against the delta spec

## 8. Integration verification

- [x] 8.1 Run the full offline suite: `uv run pytest`. Verify: no failures, no skipped tests
- [x] 8.2 Live acceptance against the gateway (container, monitor mode): send one OpenAI request with a non-streamed response containing `reasoning_content`, one streamed OpenAI response with `delta.reasoning_content` chunks, and one Anthropic pair with `thinking` blocks. Verify the events file shows no `uninspected_content` events for reasoning content, a `verdict` with `field: "reasoning_text"` when the canary is echoed only in reasoning, and (for a request with a genuinely unknown delta field) exactly one deduplicated `unknown_part` event with `count` > 1; confirm the same via `scripts/lab_monitor.py`
