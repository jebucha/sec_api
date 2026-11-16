# Proposal

## Why

Monitor-mode traffic from agent clients emits two `uninspected_content` events on essentially every request. A direct capture of the upstream stream verified that the recurring `unknown_part` gap ("non-text delta fields present", e.g. count 235) is exactly the upstream mirroring its chain-of-thought into a second field name, `reasoning`, alongside `reasoning_content` — reasoning text the gateway already collects under the first name, plus a non-streaming path (`message.reasoning`) that is silently ignored with no event at all. The second gap is the request's `tools[]` definitions, uninspected in v1, so detection rules never see tool schemas where credentials can be hardcoded. Together these gaps blind the gateway to real content and drown the event log in noise, defeating the point of running monitor mode to learn what is in the traffic.

## What Changes

- The OpenAI-compatible `reasoning` field becomes a known alias of `reasoning_content`: streamed `delta.reasoning` and non-streaming `message.reasoning` are extracted into `reasoning_text` with per-unit fallback (`reasoning_content` preferred when both are present), so mirrored upstreams produce one segment, not two, and no `uninspected_content` events. The non-streaming silent blind spot (no event, not inspected) is closed.
- A new canonical field `tool_definitions` extracts request tool definitions (OpenAI `tools[]`/legacy `functions[]`, Anthropic `tools[]`) as one deterministic JSON segment per tool, normalized so both wire formats produce identical segments for the same tool.
- `tool_definitions` becomes a valid rule `target` value (rule schema + loader), and request-stage tool definitions are no longer recorded as uninspected; malformed tool entries (non-objects, and OpenAI `tools[]` objects that are not `function`-type entries with a `function` object) remain a `tool_definitions` gap.
- Starter secrets rules (`SE-*`) widen their `target` to include `tool_definitions` so credentials embedded in tool schemas are detected by default; other starter packs keep their current targets (design D2).
- Behavior changes (intended, not breaking): `uninspected_policy: fail_closed` deployments no longer block requests whose only uninspected content was `tools[]`, or responses whose only uninspected content was a mirrored `reasoning` field; traffic of the observed shape (text + reasoning + tool definitions) emits zero `uninspected_content` events; verdict events can now carry matches with field `tool_definitions`.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `content-normalization`: `reasoning_text` gains the OpenAI-compatible `reasoning` alias (streamed and non-streaming, per-unit fallback, no duplication, no gap); a new `tool_definitions` canonical field is extracted at the request stage in both formats; the uninspected set shrinks (tool definitions removed; malformed tool entries and non-string alias values remain gaps).
- `detection-rules`: starter rule packs — the request-stage secrets rules — must target `tool_definitions` in addition to `user_text` and `tool_results` so the new channel is covered by default.

## Impact

- Code: `src/llmgw/formats/openai.py` (request parser `tools[]`/`functions[]` extraction, non-streaming `message.reasoning`, stream `delta.reasoning` + non-text catch-all), `src/llmgw/formats/anthropic.py` (request `tools[]` extraction), `src/llmgw/formats/canonical.py` (new field, `CANONICAL_FIELDS`, segment builder, docstring), `src/llmgw/rules/loader.py` + `rules/schema.json` (target enum), `rules/secrets.jsonl` (targets + version bumps).
- Events: `uninspected_content` volume drops to zero for the observed traffic shape (no more `tool_definitions` request gaps, no more `unknown_part` stream gaps); `verdict` events can now name field `tool_definitions`.
- Tests: `tests/test_formats.py` (replace T-CONTENT-NORMALIZATION-010, add alias + definition scenarios), `tests/test_parity.py`, `tests/test_engine.py`, `tests/test_proxy.py`, `tests/test_starter_packs.py`, `tests/test_rules_loader.py`, `tests/test_canonical.py`, `tests/test_rules_doc.py`.
- Operations: no config or API changes. `docs/rules.md` gains the `tool_definitions` target and its segment shape.
- Rules version bump: SE-001 through SE-004 version bumped from `2` to `3` to reflect the widened `target` field. Downstream consumers that compare rule versions for compatibility should treat this as a minor rule pack update (non-breaking — only adds a target, does not change patterns or actions).
