# Design

## Context

See proposal.md for motivation. Verified current behavior (this design was grounded in a live capture of the deployed vLLM/Qwen3 upstream):

- **OpenAI non-streaming**: `message.reasoning_content` is read by neither the request nor response parser and is **not** recorded as a gap — silently dropped.
- **OpenAI streaming**: `delta.reasoning_content` falls into the `non_text` set in `extract_stream_text` (`src/llmgw/formats/openai.py`), producing one `unknown_part` gap ("non-text delta fields present") **per chunk**.
- **Anthropic non-streaming**: `thinking` blocks fall through `_gap_kind_for_block` to `unknown_part` ("unrecognized content block type").
- **Anthropic streaming**: `content_block_start` with `type: "thinking"` gaps at start; each `thinking_delta` and `signature_delta` gaps as "non-text delta type".
- The detection engine (`src/llmgw/engine.py`) iterates canonical fields via `getattr(canonical, field)` — adding a field requires no engine change.
- `uninspected_content` events are emitted in `_emit_gaps` (`src/llmgw/proxy.py`), one event per `Gap`; `fail_closed` keys off `bool(canonical.uninspected)`.

The `inspect-tool-content` change established the pattern this change follows: new content class → new canonical field, extraction in all three entry points per format (`parse_request`, `parse_response`, `extract_stream_text`), one D1-style segment per logical unit, malformed variants remain gaps.

## Goals / Non-Goals

**Goals:**
- Thinking content is inspected in both formats at both stages, streamed and non-streamed, with cross-format parity.
- Verdicts can distinguish reasoning content from the visible answer (`field: "reasoning_text"`).
- `uninspected_content` volume no longer scales with stream chunk count.

**Non-Goals:**
- Inspecting request tool definitions (`tools[]`) — still deferred, unchanged.
- Normalizing non-standard thinking field names from other providers (e.g. `reasoning` instead of `reasoning_content`) — they remain (deduplicated) `unknown_part` gaps until observed in real traffic.
- Reasoning-content-specific heuristics beyond the existing canary pack.

## Decisions

**D1: A distinct canonical field `reasoning_text`, not a fold into `assistant_text`.**
Add `reasoning_text: tuple[str, ...]` to `Canonical` and to `CANONICAL_FIELDS`, and add `"reasoning_text"` to the rule schema `target` enum — the embedded `RULE_SCHEMA` in `src/llmgw/rules/loader.py` and the published `rules/schema.json`, which a loader test keeps equal — and to replay `TEXT_FIELDS` (`src/llmgw/replay/corpus.py`). Engine, verdicts, and excerpt handling work unchanged.
*Rationale:* matches the project's established pattern (`tool_calls`, `tool_results`), and a match in thinking must be triageable as such — an echoed canary *in thinking* is a different incident than one in the answer.
*Alternative considered:* append to `assistant_text` — rejected: verdicts become ambiguous and "Response normalization" (assistant_text = generated *visible* text) would silently change meaning.

**D2: One segment per message, or per accumulated stream.**
Non-streaming: each message/block's reasoning text is one segment (OpenAI: per `message.reasoning_content`; Anthropic: per `thinking` block), mirroring `assistant_text` granularity. Streamed: all fragments concatenate into a single segment at stream end, mirroring how stream text is accumulated today.
*Alternative considered:* one segment per delta fragment — rejected for the same reason as tool-call reconstruction (inspect-tool-content D4): fragmented text is pattern-hostile and breaks parity with non-streaming responses.

**D3: Extraction sites.**
- OpenAI `parse_request`/`parse_response`: read `message.reasoning_content` (string) into the reasoning bucket; non-string values become an `unknown_part` gap at that location.
- OpenAI `extract_stream_text`: append string `delta.reasoning_content` to `reasoning_parts`; add `"reasoning_content"` to the whitelist tuple so it no longer counts as a non-text delta field.
- Anthropic `_route_block`/`_parse_blocks`: handle `type: "thinking"` blocks into a reasoning bucket (available at request history and response stages); non-string `thinking` text is a gap.
- Anthropic `extract_stream_text`: `content_block_start` `type: "thinking"` opens a slot keyed by block index; `thinking_delta` `thinking` fragments accumulate into it; `content_block_stop` closes it into a segment; `signature_delta` is treated as protocol-only (ignored, no gap — D7). An unopened `thinking_delta` is a gap.
- Anthropic `content_block_start` for other unknown block types still gaps as today.

**D4: Replay corpus and docs.**
`reasoning_text` joins `TEXT_FIELDS` so the replay harness can seed it; `docs/rules.md` field reference gains the new field and notes that thinking content is now inspected.

**D5: Starter pack: canary-leak (PL-001) targets `reasoning_text` in addition to `assistant_text`.**
Other starter packs are request-stage `user_text`/`tool_results` rules; request-history thinking is rare in practice and can be added later without spec rework (the field will already be targetable).
*Alternative considered:* add `reasoning_text` to every pack — rejected: no demonstrated need, and response-stage secrets/PII coverage is a deliberate future decision.

**D6: Deduplicate `uninspected_content` at the event boundary, not in the parsers.**
`_emit_gaps` groups gaps per stage by (kind, location, detail) and emits one event per group with a `count` field (every event carries `count`; `1` for a single gap). `uninspected_content_event` gains `count: int`. `Canonical.uninspected` keeps every gap, so `fail_closed` semantics and any future reporting are unaffected.
*Alternative considered:* dedup inside the format parsers — rejected: the canonical structure is the single source of truth; collapsing there loses information the gateway itself relies on.
*Trade-off accepted:* the per-gap *sequence* is no longer visible in events; `count` preserves the magnitude.

**D7: Anthropic `signature_delta` is protocol-only.**
It carries a cryptographic signature of the thinking block, not content; inspecting or flagging it is noise.
*Alternative considered:* flag as `unknown_part` — rejected: it would recreate exactly the per-chunk noise this change removes, for a field that is not content.

## Risks / Trade-offs

- [Fail-closed deployments block thinking traffic today (Anthropic gaps; OpenAI stream gaps) and will no longer] → Intended, and consistent with the `inspect-tool-content` precedent for tool content; monitor mode is the default, and live acceptance (Migration Plan step 1) verifies before any fail-closed rollout.
- [A provider uses a non-standard thinking field (e.g. `reasoning`)] → Remains an `unknown_part` gap, now one deduplicated event per stage instead of dozens — visible, not silent; extend D3 when observed.
- [vLLM without a reasoning parser leaks thinking into `content`] → Still inspected, as `assistant_text` — no regression, out of scope.
- [`count` changes the `uninspected_content` event shape] → Additive field only; JSON consumers tolerate unknown fields; documented in the delta spec.
- [Thinking text in excerpts] → Bounded by `excerpt_max_chars` like any field; `mask_excerpt` rules apply as targeted.

## Test Strategy

All new tests are offline pytest cases run from the project root with `uv run pytest` (no container or network required), following the existing per-area files: `tests/test_formats.py` (extraction, both formats), `tests/test_parity.py` (cross-format parity), `tests/test_engine.py` (rule evaluation), `tests/test_starter_packs.py` (pack behavior), `tests/test_proxy.py` (event emission). New tests are named after their `T-<CAPABILITY>-<NNN>` IDs. Live acceptance (task 8.2) runs the gateway container in `monitor` mode against `scripts/fake_upstream.py` via `scripts/lab_monitor.py`.

## Migration Plan

1. Ship behind the normal image build; run in `monitor` mode first: verify `uninspected_content` volume for thinking traffic drops to zero (or one deduplicated event for genuinely unknown fields), and that a canary planted in the system prompt and echoed only in reasoning content produces a `verdict` with `field: "reasoning_text"`.
2. Live acceptance against the gateway container (same shape as `inspect-tool-content` task 8.2): one OpenAI request/response pair with `reasoning_content` (non-streamed and streamed) and one Anthropic pair with `thinking` blocks, via `scripts/lab_monitor.py` against the fake upstream.
3. Rollback: redeploy the previous image; no data or schema migration.

## Open Questions

- Should request-stage starter packs (injection/secrets/PII) target `reasoning_text` for request-history thinking? Defer until such traffic is observed; the field is already targetable.
- Should the `unknown_part` "non-text delta fields present" detail name the offending field(s)? Field names are not raw content, but naming them is a deliberate follow-up, not part of this change.
