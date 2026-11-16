# Design

## Context

See proposal.md for motivation. Current state that constrains the approach:

- **The mystery field is identified and verified.** A direct capture of the monitored upstream's stream yielded the delta field map `role: 1, content: 10, reasoning_content: 235, reasoning: 235` — and the gateway's `unknown_part` event for the same request carried `count: 235`. The counts match exactly: every "non-text delta field" chunk is the upstream mirroring its chain-of-thought into `reasoning` alongside `reasoning_content`. There is no other unknown content in this traffic, which makes an open-world "inspect all unknown fields" design unnecessary.
- **The stream parser** (`formats/openai.py` `extract_stream_text`, ~line 473) knows exactly four delta fields — `role`, `content`, `reasoning_content`, `tool_calls` — and records one `unknown_part` gap per chunk for anything else non-null. The catch-all is what produces the log noise.
- **The non-streaming parser** (`formats/openai.py` `parse_response`) reads `reasoning_content`, `content`, `tool_calls`, `function_call` only. A `message.reasoning` field is **silently ignored** — not even a gap, no event. Closing the alias closes this silent blind spot.
- **Tool definitions** (`tools[]` in both formats, legacy `functions[]` in OpenAI) are recorded as `GAP_TOOL_DEFINITIONS` ("not inspected in v1") at `formats/openai.py:~177` and `formats/anthropic.py:~239` and never enter the canonical structure.
- The engine (`engine.py:104-106`) evaluates a rule's `target` fields via `getattr(canonical, field)`, so a new `Canonical` field needs no engine change. The closed `target` enum lives in two places: `rules/schema.json` (~line 30) and `src/llmgw/rules/loader.py:54-61`.
- `tool_calls` segments (archived change `inspect-tool-content`, D1) are deterministic `sort_keys=True` JSON built by a shared helper in `formats/canonical.py`; cross-format parity is enforced by tests in `tests/test_parity.py`.

## Goals / Non-Goals

**Goals:**

- Zero `uninspected_content` events for the observed traffic shape (text + mirrored reasoning + tool definitions), in both formats, streamed and non-streaming.
- `reasoning` treated as a first-class alias of `reasoning_content` with per-unit fallback; no duplicate segments for mirrored upstreams.
- Tool definitions extracted into one deterministic, cross-format-identical segment per tool, rule-targetable via the existing engine.

**Non-Goals:**

- Inspecting images, or changing any other gap kind (`stream_error`, `stream_overflow`, `other`, non-text content parts).
- Open-world inspection of arbitrary unknown delta fields (the capture showed there is nothing left to inspect here).
- Any debug/excerpt mode for learning what is in the traffic (possible follow-up change).
- Extending non-secrets starter packs to `tool_definitions` (design D2).
- Any config, proxy-header, or event-envelope changes.

## Decisions

**D1: `reasoning` is an alias with per-unit fallback, not a union.**
Per message (non-streaming) or per delta (streaming): if `reasoning_content` is present (and a string), use it; else if `reasoning` is present (and a string), use it; else nothing. A non-string value in either field is malformed reasoning content → `unknown_part` gap at that location (consistent with T-CONTENT-NORMALIZATION-033). The `reasoning` key is added to the stream parser's known-fields set, so the non-text catch-all no longer fires for it.
*Alternatives considered:* (a) union/concatenation of both values — rejected: the observed upstream mirrors identical text into both fields, so a union doubles every reasoning segment and risks double matches on every reasoning rule; (b) open-world JSON-encoding of any unknown delta field — rejected: verified overkill (235/235 match), and it enlarges the behavior surface for fields that do not exist in this traffic; (c) whitelisting `reasoning` as ignored-noise — rejected: it is reasoning content, and the gateway already inspects it under the other name — the *duplicate* is the noise, not the text.
*Accepted edge case:* an upstream that puts *differing* content in the two fields loses the `reasoning` value (fallback prefers `reasoning_content`). Documented, not detected; no known upstream behaves this way.

**D2: Starter packs — only the secrets rules widen to `tool_definitions`.**
Extend the `target` of the request-stage secrets rules (`SE-001`–`SE-004`) from `["user_text", "tool_results"]` to `["user_text", "tool_results", "tool_definitions"]`, with a `version` bump per rule (docs/rules.md: "bump on change"). Rationale: tool definitions are static schema boilerplate — prompt-injection, jailbreak, PII, and smuggling patterns tuned on message text carry real false-positive risk against descriptions and parameter schemas, and new verdicts from them would muddy the operator's monitor-mode baseline. Secret *shapes* (key formats, token patterns) are unambiguous, essentially absent from legitimate boilerplate, and a hardcoded credential in a tool description is a realistic exfiltration vector. This keeps the precedent of the `inspect-tool-content` change (new channel covered by default) without over-reach.
*Alternatives considered:* (a) widen all tool-targeting packs — rejected, false-positive risk on boilerplate (above); (b) widen none — rejected, leaves the channel uncovered by default and contradicts the established precedent. Revisit after the operator has monitor-mode verdict data on real tool schemas.

**D3: Tool definition segment shape — one normalized JSON object per tool, identical across formats.**
Each well-formed tool definition becomes exactly one `tool_definitions` segment:

```json
{"description": "…", "input_schema": {…}, "name": "shell"}
```

serialized with `sort_keys=True` (missing `description`/schema → `null`), built by a shared helper in `formats/canonical.py` alongside `tool_call_segment`. OpenAI contributes `tools[].function.name`/`description`/`parameters` (legacy: top-level `functions[]` entries directly); Anthropic contributes `tools[].name`/`description`/`input_schema`. Both parameter schemas are JSON Schema objects, so normalizing them under one key (`input_schema`) makes semantically identical definitions byte-identical segments — the parity test (T-CONTENT-NORMALIZATION-040) enforces this.
*Alternatives considered:* (a) per-format native shapes — rejected, breaks parity and forces two patterns per concept (same argument as tool-call D1); (b) name/description/schema as separate segments — rejected, defeats "one pattern sees the whole tool" and a schema-embedded secret adjacent to its name; (c) raw wire bytes — rejected, format noise (e.g. `{"type": "function"}` wrapper) leaks into rule writing.

**D4: Malformed tool entries stay a gap; the rest still extracts.**
A `tools[]`/`functions[]` entry that is not an object (and an OpenAI `tools[]` object that is not a `function`-type entry with a `function` object) is recorded as a `GAP_TOOL_DEFINITIONS` gap at its positional location; well-formed siblings are still extracted. The gap kind is retained for diagnostics rather than removed. Non-object definitions are the only `tool_definitions`-kind gap that remains; the "not inspected in v1" blanket gap goes away.

**D5: Non-streaming silent blind spot is closed by extraction, not by a gap.**
`message.reasoning` is extracted per D1 (it is well-formed reasoning content), so the previous silent drop becomes a first-class segment. No new gap is introduced for it. If any existing test asserts the old silent behavior, it is updated (none were found at planning time).

## Risks / Trade-offs

- [Fail-closed deployments forward more traffic] → Intended: requests whose only uninspected content was `tools[]`, and responses whose only uninspected content was a mirrored `reasoning` field, are now fully inspected, so `fail_closed` (keyed on `bool(canonical.uninspected)`) stops firing on them. Documented in the proposal; the operator's fail-closed guarantee is *stronger*, not weaker — less content is uninspected, never more.
- [New verdict volume on `tool_definitions`] → Bounded by D2 (secrets patterns only, on static boilerplate); monitor mode allows a trial period before enforce. `mask_excerpt: true` on secrets rules keeps matched values out of events.
- [Alias fallback silently prefers `reasoning_content` when both fields differ] → Accepted edge case (D1); no known upstream diverges the two fields, and both values are the model's own reasoning, not untrusted client content.
- [Tool schema boilerplate growing with agent toolkits raises per-request CPU] → Linear per rule (re2); request bodies are hard-capped by `max_request_bytes`; only secrets rules target the field (D2), so cost is ~4 linear scans over static text.
- [Cross-format parity couples the two parsers to one encoding] → Concentrated in the D3 helper shared by both format modules, mirroring the tool-call precedent; T-CONTENT-NORMALIZATION-040 enforces it.

## Migration Plan

1. Deploy the parser + schema changes; config and API are unchanged, so the existing container restart path applies.
2. Run in `monitor` mode: verify `uninspected_content` volume drops to zero for agent traffic and that any new `tool_definitions` verdicts are legitimate.
3. Flip to `enforce` when satisfied. Rollback = previous image; the rules directory is unchanged in shape (one new enum value, backward-compatible).

## Test Strategy

- Runner: `uv run pytest` from the repo root; a single test runs as `uv run pytest <path>::<test>`. The suite is offline — upstream traffic is mocked with `httpx.MockTransport` (see `tests/helpers.py`), so no network access or running gateway is required for any unit or integration test.
- Lint: `uv run ruff check` from the repo root; a clean exit is part of the completion criteria.
- The Test Requirement blocks in the spec deltas reference this section instead of repeating runner or environment details.
- Live acceptance (task 7.2) runs against the containerized gateway in `monitor` mode; it is outside the automated suite.

## Open Questions

None — the scope (tool definitions + the verified `reasoning` field) came from the operator's own monitor-mode traffic, and D1–D5 resolve the remaining technical choices.
