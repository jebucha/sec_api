# Proposal

## Why

Thinking models (e.g. Qwen3 via vLLM) stream their chain of thought as a first-class content channel — `reasoning_content` in OpenAI-format deltas/messages and `thinking` blocks in Anthropic format — that the gateway does not inspect. Today this surfaces three ways: OpenAI non-streaming `reasoning_content` is **silently dropped** (neither inspected nor flagged), OpenAI stream `reasoning_content` deltas and Anthropic `thinking` blocks fire one `unknown_part` event **per chunk** (dozens of identical `uninspected_content` events per request, drowning out real signals). Thinking text is a genuine exfiltration and injection surface: a model can echo a canary token, secret, or injected instruction inside its thinking without it ever appearing in the visible answer.

## What Changes

- New canonical field `reasoning_text` (list of segments, like `assistant_text`), extracted in both formats at both stages: OpenAI `reasoning_content` (non-streaming `message` field and accumulated stream deltas) and Anthropic `thinking` blocks (non-streaming content blocks and accumulated `thinking_delta` stream events).
- `reasoning_text` becomes a rule-targetable canonical field: added to the rule schema's field enum; the detection engine evaluates it with no engine changes (it already iterates canonical fields generically).
- Starter rule pack update: the canary-token prompt-leak detector (PL-001) targets `reasoning_text` in addition to `assistant_text`, so a canary echoed in thinking is detected at the response stage.
- `uninspected_content` events are **deduplicated per stage**: at most one event per unique (kind, location, detail) combination per stage, carrying a `count` of the gaps it aggregates. `fail_closed` behavior is unchanged (it keys off presence of any gap).
- Anthropic stream `signature_delta` events (cryptographic thinking signatures, not content) are treated as protocol-only: ignored, no gap.
- Non-text delta field gaps on OpenAI streams (the "non-text delta fields present" case) remain for fields other than `reasoning_content`; they simply become one event per stage after dedup.

No breaking changes: existing rules, events, and config are unchanged except that `uninspected_content` events gain a `count` field and the rule schema accepts one additional target value.

## Capabilities

### New Capabilities

(None — this extends existing capabilities.)

### Modified Capabilities

- `content-normalization`: canonical field list gains `reasoning_text`; new requirement for reasoning-content extraction (both formats, both stages, streamed and non-streamed); "Uninspected content is flagged" modified for per-stage event dedup with `count`.
- `detection-rules`: rule schema field enum gains `reasoning_text`; starter rule packs requirement modified so the canary-leak pack targets `reasoning_text`.

## Impact

- Code: `src/llmgw/formats/canonical.py` (field), `src/llmgw/formats/openai.py` (non-streaming + stream extraction), `src/llmgw/formats/anthropic.py` (blocks + stream extraction), `src/llmgw/rules/loader.py` and `rules/schema.json` (schema enum, kept in parity by a test), `src/llmgw/replay/corpus.py` (TEXT_FIELDS), `src/llmgw/proxy.py` (`_emit_gaps` dedup), `rules/prompt-leak.jsonl` (PL-001 target).
- Events: `uninspected_content` gains `count`; volume of `unknown_part` events for thinking traffic drops from per-chunk to one per stage (or zero once extracted).
- Verdicts: matches can now carry `field: "reasoning_text"`.
- Docs: `docs/rules.md` field reference.
