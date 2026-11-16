# Design

## Context

See proposal.md for motivation. Current state that constrains the approach:

- The canonical structure (`formats/canonical.py`) already carries `tool_calls` and `tool_results` as empty seams; `CANONICAL_FIELDS`, the rule schema's `target` enum (`rules/loader.py:54`), and the engine's `getattr(canonical, field)` loop (`engine.py:104-106`) all already support them. **No schema, loader, or engine changes are needed** — only the format parsers start producing content.
- Both format modules expose three entry points that build canonical content: `parse_request`, `parse_response`, and `extract_stream_text` (accumulated SSE → `(text, gaps)` tuple). All three must gain tool-content extraction.
- Streamed responses are buffered whole before inspection in both modes (monitor tee, enforce replay) under `max_buffer_bytes`; the raw stream bytes are available once, so stream tool-call reconstruction happens at the same point as text extraction.
- The request body is already fully buffered (capped by `max_request_bytes`); tool content adds no new memory path.
- Starter packs are JSONL rule files under `rules/`; rule `tests.match`/`no_match` self-tests exercise the matcher directly and are independent of `target`, so widening a rule's `target` array breaks no self-test.

## Goals / Non-Goals

**Goals:**

- One canonical segment per tool call (both stages, both formats, non-streaming and accumulated streams) and per tool result text (request stage, both formats).
- Cross-format parity: the same logical call/result yields identical canonical segments from `/v1/chat/completions` and `/v1/messages`.
- Zero new config, zero new API surface; existing gap/fail-closed machinery keeps working on the (smaller) uninspected set.

**Non-Goals:**

- Inspecting `tools[]` definitions (remains uninspected; user decision).
- Inspecting legacy `function_call` fields (remains uninspected).
- Per-tool-name or per-argument sub-segmenting (user chose one full-JSON segment per call).
- Any rule-schema, engine, event-envelope, or proxy-header changes.
- Matching across segment boundaries (existing guarantee, unchanged).

## Decisions

**D1: Tool call segment shape — one canonical JSON object per call, normalized across formats.**
Every tool call (request history or response-generated) becomes exactly one segment:

```json
{"input": {…arguments object…}, "name": "shell"}
```

serialized with `sort_keys=True`. OpenAI entries contribute `function.name` and `function.arguments`; the `arguments` string is parsed as JSON (lenient) so both formats produce the *same* object — OpenAI's stringified-JSON arguments become the `input` object. If the arguments string does not parse, `input` is the raw string (still one segment, still inspectable). Anthropic `tool_use` blocks contribute `name` and `input` directly.
*Alternatives considered:* (a) per-format native shapes — rejected, breaks the parity scenario and forces rule authors to write two patterns per concept; (b) name and arguments as separate segments — rejected by user (would make "name AND args" rules impossible with one pattern); (c) raw wire bytes as the segment — rejected, format-specific noise would leak into rule writing.

**D2: Tool result segments — text only, one per text unit.**
OpenAI `role: "tool"` message content (string) is one segment, verbatim; non-string content (e.g. an array of parts) is recorded as a gap of kind `tool_result`. Anthropic `tool_result` block: string content is one segment; array content yields one segment per text part. Non-text parts (images) remain gaps. `tool_call_id` is deliberately not part of any segment: it is an opaque id with no rule value, and including it would vary the segment across formats.

**D3: `extract_stream_text` becomes a full stream canonicalizer.**
Change both formats' stream entry point from `(text, gaps) -> (str, tuple[Gap])` to return a `Canonical` (assistant text + reconstructed tool calls + gaps). The two call sites in `proxy.py` (monitor tee, enforce replay) construct the canonical from it directly, replacing the current `Canonical(assistant_text=…, uninspected=…)` construction.
*Alternative:* a parallel `extract_stream_tool_calls` function — rejected, it would parse the same SSE bytes twice and the two results would need to agree on gap accounting.

**D4: Stream tool-call reconstruction.**
OpenAI: accumulate `choices[].delta.tool_calls[]` fragments by `index` — `function.name` from the first fragment, `arguments` string fragments concatenated; on stream end each complete index yields one D1 segment. Fragments that cannot be accumulated (missing index, non-object entry, empty result) become a gap with kind `tool_call` and a detail string (never raw content). Anthropic: a `content_block_start` with `type: "tool_use"` opens a slot keyed by block index (captures `name`); `input_json_delta` `partial_json` fragments concatenate into it; `content_block_stop` closes it and yields one D1 segment (parsed `input`, or raw string if the accumulated JSON does not parse). A `content_block_start` for a non-text, non-tool block type still produces today's gap. An `input_json_delta` arriving with no open `tool_use` slot is recorded as a gap of kind `unknown_part` (today's behavior, preserved).
*Alternative:* inspect concatenated raw fragment text without reconstruction — rejected, fragmented JSON would be a pattern-hostile corpus and would break parity with non-streaming responses.

**D5: Remaining gaps.**
`GAP_TOOL_CALL`/`GAP_TOOL_RESULT` constants stay for the malformed-content cases: non-list `tool_calls`, non-object call entries, legacy `function_call`, unparseable stream reconstruction. Well-formed tool content no longer produces any gap, so `uninspected_content` events of those kinds stop for healthy traffic, and `fail_closed` (which keys off `bool(canonical.uninspected)`) automatically stops firing on tool content.

**D6: Starter packs — widen existing `target` arrays instead of adding parallel rules.**
Extend the `target` of the prompt-injection rules (`PI-*`), the secrets rules, and the PII rules from `["user_text"]` to `["user_text", "tool_results"]` (request stage). This reuses already-reviewed patterns, severities, and inline self-tests; a tool result containing an injection marker or a secret matches the same rule, with the verdict event's `field` identifying `tool_results`. Jailbreak, smuggling, and canary-leak packs keep their current targets: jailbreak framing in tool output is covered by the injection rules' markers, smuggling is an encoding concern of user text, and canary leak is response-stage.
*Alternative:* new `PI-T*`-style rules dedicated to `tool_results` — rejected as duplication; the detection content is identical, only the field differs, and `target` exists exactly for this.

**D7: No size cap on tool segments.**
Segments are inspected in full (re2 is linear; T-DETECTION-RULES-006 already bounds pathological patterns). The request body and stream buffer are already hard-capped upstream (`max_request_bytes`, `max_buffer_bytes`), so inspection cost is bounded by existing limits. Excerpt capture for matches stays at `excerpt_max_chars`.

## Risks / Trade-offs

- [Existing rules targeting `tool_calls`/`tool_results` start matching] → This is the intended fix, not a defect; documented in the proposal as a behavior change. Operators running unknown third-party packs with tool targets should watch verdict volume after deploy.
- [False positives from legitimate tool output matching injection/secret patterns] → Expected trade-off of inspection; severities/actions are the operator's dial (flag/block), and monitor mode allows a trial period before enforce. `mask_excerpt: true` on secret/PII rules already keeps matched values out of events.
- [Large tool results increase per-request CPU (rules × segment length)] → Linear per rule (D7); the 1 MB scenario in the spec pins the expectation. If a deployment with very large results and large rule packs shows latency regressions, the remedy is rule-set tuning, not a parser cap (a cap would reintroduce a blind spot).
- [Stream reconstruction diverges from wire reality for malformed streams] → Reconstructed-from-gaps: any fragment we cannot accumulate is a gap (D5), never silently dropped; parity tests only pin well-formed streams.
- [Cross-format parity couples the two parsers to one encoding] → Concentrated in one helper (D1) shared by both format modules; the parity test (T-CONTENT-NORMALIZATION-024) enforces it.

## Migration Plan

1. Deploy the parser changes; no config, schema, or API change, so the existing container restart path applies.
2. Run in `monitor` mode (default) first: verify `uninspected_content` volume drops and that any new verdicts are legitimate.
3. Flip to `enforce` when satisfied. Rollback = previous image; the only state is the rules directory, which is unchanged in shape.

## Test Strategy

- Runner: `uv run pytest` from the repo root; a single test runs as `uv run pytest <path>::<test>`. The suite is offline — upstream traffic is mocked with `httpx.MockTransport` (see `tests/helpers.py`), so no network access or running gateway is required for any unit or integration test.
- Lint: `uv run ruff check` from the repo root; a clean exit is part of the completion criteria.
- The Test Requirement blocks in the spec deltas reference this section instead of repeating runner or environment details.
- Live acceptance (task 8.2) runs against the containerized gateway in `monitor` mode with `scripts/lab_monitor.py`; it is outside the automated suite.

## Open Questions

None — the user's two scoping decisions (results+calls scope; full-JSON call segments) are recorded in the proposal, and D1-D7 resolve the remaining technical choices without changing specs.
