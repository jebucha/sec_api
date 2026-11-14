# Tasks

## 1. Project scaffolding

- [x] 1.1 Edit the existing `pyproject.toml` per design D1 (do not run `uv init`): keep project name `sec-api`, set `requires-python = ">=3.13,<3.15"`, add a `uv_build` build backend with `module-name = "llmgw"`, create `src/llmgw/__init__.py`, and pin `.python-version` to `3.13`. Verify `uv run python -c "import llmgw"` succeeds
- [x] 1.2 Add runtime deps with `uv add` (starlette, uvicorn, httpx, google-re2, jsonschema, pyyaml, watchfiles, python-ulid) and dev deps with `uv add --dev` (pytest, pytest-asyncio, ruff), then verify `uv sync` and `uv run python -c "import re2"` succeed on the host
- [x] 1.3 Configure ruff and pytest (`asyncio_mode = "auto"`) in `pyproject.toml`, add `tests/test_smoke.py` asserting `import llmgw` works (so pytest collects at least one test), and verify `uv run ruff check` and `uv run pytest` both exit 0

## 2. Security events

- [x] 2.1 Implement `events.py`: envelope (`event_id`/`request_id` ULIDs, RFC 3339 UTC timestamp, `event_type`, `client`, `mode`, `ruleset_hash`, nulls for n/a) and a JSONL writer (append-mode file, optional stdout, one write per line). Verify with `tests/test_events.py` (T-SECURITY-EVENTS-001, -002)
- [x] 2.2 Add a redaction guard test helper that scans emitted events for known secret values (tokens, upstream keys) and verify it fails when a secret is injected into an event

## 3. Configuration and client auth

- [x] 3.1 Implement `config.py`: load `gateway.yaml` into typed dataclasses (D10, including `max_request_bytes` and `canary_tokens_env`), resolve `secret_env`/`secret_file`, and fail at startup on missing secrets or unknown upstream references in `models`. Verify with tests for valid config, missing secret, and dangling model->upstream reference
- [x] 3.2 Implement `auth.py`: extract tokens from `Authorization: Bearer` or `x-api-key`, sha256 them, and compare with `hmac.compare_digest` against configured client hashes. Verify with `tests/test_auth.py` (T-CLIENT-AUTH-001..006, T-LLM-PROXY-026)
- [x] 3.3 Add the `llmgw-token` script entry point that prints a new random token and its sha256. Verify that `uv run llmgw-token demo` outputs both and that the hash authenticates the token in a test
- [x] 3.4 Write `config/gateway.example.yaml` and a "Configuration & tokens" section in `README.md`, and verify the example loads via a test that parses it

## 4. Canonical normalization

- [x] 4.1 Implement `formats/canonical.py` (segment lists per D5, including empty `tool_calls`/`tool_results` and `uninspected` gaps) and verify dataclass construction and equality in tests
- [x] 4.2 Implement `formats/openai.py` request/response parsing: system/developer messages, string and multi-part content, prior assistant turns, `tool_calls`/`tools[]`/images recorded as uninspected, unknown part types tolerated, duplicate-key rejection (content-normalization: Duplicate JSON keys rejected). Verify with `tests/test_formats.py` fixture tests for the OpenAI side of T-CONTENT-NORMALIZATION-002..011
- [x] 4.3 Implement `formats/anthropic.py` request/response parsing: top-level `system` (string and blocks), text blocks, `tool_use`/`tool_result`/`tools[]`/image as uninspected, duplicate-key rejection. Verify with `tests/test_formats.py` fixture tests for the Anthropic side
- [x] 4.4 Implement SSE text-delta extraction for both formats (OpenAI `choices[].delta.content`, Anthropic `content_block_delta.delta.text`, other events as uninspected). Verify against recorded stream fixtures (including chunk boundaries that split an SSE line)
- [x] 4.5 Implement provider-shaped error bodies (400/401/403/404/413/502/504, type `gateway_blocked` for blocks) for both formats. Verify that the shapes parse with the official error schema shapes in tests
- [x] 4.6 Add the format-parity test suite in `tests/test_parity.py`: the same payloads through both formats yield identical canonical `system`/`user_text`/`assistant_text`. Verify T-CONTENT-NORMALIZATION-001 passes

## 5. Detection rules

- [x] 5.1 Write `rules/schema.json` (all required/optional fields including `enabled`, `mask_excerpt`, `tests`, and the per-type payloads `pattern`, `keywords`/`case_sensitive`, `heuristic`/`params`) and verify it with schema tests of valid and invalid rule lines
- [x] 5.2 Implement `rules/heuristics.py` registry: `luhn_card`, `unicode_tags`, `zero_width`, `base64_run`, `canary` (reading canary values from the env var named by config `canary_tokens_env`, or from an explicit list passed by the replay tool). Verify each with unit tests in `tests/test_heuristics.py`, including Luhn false-positive rejection of random digit runs
- [x] 5.3 Implement `rules/loader.py`: read `*.jsonl` (skipping blanks and `//` lines), validate every rule including those with `enabled: false`, reject duplicate ids, compile regex/keyword with RE2, and resolve heuristics from the 5.2 registry. Each rejection emits a `rule_rejected` event with file, line, id, and reason. Verify with `tests/test_rules_loader.py` (T-DETECTION-RULES-001..005, -007, -008, -012; T-SECURITY-EVENTS-007)
- [x] 5.4 Implement inline self-tests (`tests.match`/`tests.no_match`) in the loader. Verify T-DETECTION-RULES-009 and -010
- [x] 5.5 Implement the immutable `Ruleset` holding only enabled rules as active, with a deterministic sha256 over active rules sorted by id. Verify T-DETECTION-RULES-013 and that one changed rule yields a different hash
- [x] 5.6 Add the ReDoS regression test: rule `(a+)+$` against a 100,000-char adversarial input completes in under 1 s. Verify T-DETECTION-RULES-006 passes
- [x] 5.7 Document the rule format in `docs/rules.md` (fields, per-type payloads, heuristics, self-tests, masking, RE2 limits). Verify that every example rule in the doc loads via a test that extracts and loads them

## 6. Detection engine

- [x] 6.1 Implement `engine.py` `evaluate(ruleset, canonical, stage) -> Verdict`: evaluate only enabled rules, stage and target scoping, all matches collected, precedence `block > flag > log > allow`. Verify with `tests/test_engine.py` (T-DETECTION-ENGINE-002..005, T-DETECTION-RULES-011)
- [x] 6.2 Implement monitor/enforce semantics (`enforced` flag, would-block in monitor) and rule-error handling (`rule_error` event; block in enforce, pass in monitor). Verify T-DETECTION-ENGINE-010 and -011
- [x] 6.3 Implement bounded excerpts (`excerpt_max_chars`) and `mask_excerpt` masking (`AK…LE (20 chars)`). Verify T-DETECTION-ENGINE-008 and -009, including the redaction guard from 2.2 against a secrets match
- [x] 6.4 Implement the `verdict` event builder in `events.py`: one event per non-`allow` stage verdict with `stage`, `format`, `model`, `upstream`, `action`, `enforced`, and `matches` (rule id, severity, owasp/atlas tags, canonical field, excerpt). Verify T-SECURITY-EVENTS-003

## 7. Proxy (non-streaming)

- [x] 7.1 Implement `routing.py`: model lookup (404 for an unknown model, 400 for a missing model or invalid JSON), the header forwarding policy from design D4a (client credential and hop-by-hop stripping, `Host`/`Content-Length` left to httpx, `Accept-Encoding: identity`), and per-upstream credential injection (bearer / x-api-key / none). Verify with `tests/test_routing.py` (T-LLM-PROXY-006..008, -011..013) asserting the upstream request headers for each auth style and that hop-by-hop headers are not forwarded
- [x] 7.2 Implement `app.py` routes (`/v1/chat/completions`, `/v1/messages`, `/v1/models`, `/healthz` returning `{"status":"ok"}`, 404 otherwise): generate `request_id` at arrival, set `X-Gateway-Request-Id` on every API response, enforce `max_request_bytes` with a 413 before parsing (design D4b), and load config and rules in the lifespan. Verify with `tests/test_app.py` (T-LLM-PROXY-003, -009; T-DETECTION-RULES-017, -018), T-CLIENT-AUTH-007, and T-LLM-PROXY-004
- [x] 7.3 Implement non-streaming forwarding in `proxy.py`: request-stage evaluation before forwarding, byte-identical body passthrough, and upstream response relay. Verify with `tests/test_proxy.py` against `httpx.MockTransport` (T-LLM-PROXY-001, -002, -005, -010; T-DETECTION-ENGINE-001, -006)
- [x] 7.4 Add response-stage evaluation and block responses: provider-shaped 403 with `X-Gateway-Event-Id` naming the `verdict` event, opaque messages unless `debug_verdicts`. Verify T-LLM-PROXY-020..023, T-DETECTION-ENGINE-007, and T-SECURITY-EVENTS-004
- [x] 7.5 Emit an `uninspected_content` event for every `uninspected` gap in the request or response canonical structure, and apply `uninspected_policy: fail_closed` (block regardless of mode). Verify T-CONTENT-NORMALIZATION-009, -010, -012, -013
- [x] 7.6 Implement upstream failure handling (502 on connection failure, 504 on timeout) and one `request_completed` event per API request, including auth failures (`auth_failed` + `request_completed` sharing a `request_id`). Verify T-LLM-PROXY-024, -025, -014; T-SECURITY-EVENTS-005, -006, -008; T-CLIENT-AUTH-002

## 8. Streaming

- [x] 8.1 Implement monitor-mode streaming: a tee generator yields chunks immediately and accumulates up to `max_buffer_bytes`. Past the cap it keeps relaying and emits an `uninspected_content` event of type `stream_overflow`. Response-stage evaluation and events run after the stream ends. Verify with `tests/test_streaming.py` (T-LLM-PROXY-015, -016), using a fake upstream that delays between chunks
- [x] 8.2 Implement enforce-mode streaming: buffer up to `max_buffer_bytes`, evaluate, then replay the exact original chunks or return a 403 block with no generated content. A buffer overflow counts as a detection error (block + `rule_error`). Verify T-LLM-PROXY-017..019

## 9. Hot reload

- [x] 9.1 Implement `rules/watcher.py`: debounced `watchfiles` watcher plus a SIGHUP handler that builds a new Ruleset and atomically swaps the reference, emitting `ruleset_loaded` (hash, accepted/rejected counts) or `ruleset_reload_failed`, with requests capturing the ruleset once at start. Verify with `tests/test_reload.py` (T-DETECTION-RULES-014..016)

## 10. Starter rule packs and corpus

- [x] 10.1 Write `rules/prompt-injection.jsonl` and `rules/jailbreak.jsonl` with OWASP/ATLAS tags and inline tests (including near-miss `no_match` cases). Verify that they load with zero rejections
- [x] 10.2 Write `rules/secrets.jsonl` and `rules/pii.jsonl` (`mask_excerpt: true`, Luhn heuristic for cards), `rules/smuggling.jsonl` (tags, zero-width, base64 runs), and `rules/prompt-leak.jsonl` (canary heuristic). Verify T-DETECTION-RULES-019 and -020
- [x] 10.3 Write the starter corpus in `corpus/*.jsonl`: malicious and benign entries per category, including near-miss benign entries, both `format` values, and `stage: response` prompt-leak entries that use the test-only canary `LLMGW-TEST-CANARY-0000` (design D11). Verify that each line validates against the corpus format

## 11. Replay tool

- [x] 11.1 Implement `replay/cli.py` offline mode (reusing loader and engine): build the canonical structure from `field` and evaluate at the entry's `stage`, add a `--canaries` option, compute metrics overall, per category, and per rule using the detection-testing formulas (TP/FP/FN/TN, detection rate, FP rate, null on zero denominator, unexpected matches listed), output a human table and a JSON report, report invalid lines, and apply threshold flags with a non-zero exit on violation. Verify with `tests/test_replay.py` (T-DETECTION-TESTING-001..003, -005..008, -010)
- [x] 11.2 Implement live mode: send request-stage entries as real requests in their `format` with a token, correlate each response with events via its `X-Gateway-Request-Id` header and the events file, and report response-stage entries as skipped. Verify T-DETECTION-TESTING-004 against the in-process app and fake upstream
- [x] 11.3 Run `uv run llmgw-replay --corpus corpus/ --rules rules/ --canaries LLMGW-TEST-CANARY-0000` and tune starter rules until every category has at least one TP and benign entries are evaluated. Record the baseline report in `docs/baseline.md`. Verify T-DETECTION-TESTING-009 and that the documented command reproduces the numbers
- [x] 11.4 Document the replay tool usage, the corpus fields (including `stage`), and the test-only canary in `docs/testing.md`, and verify the documented commands run as written

## 12. Container

- [x] 12.1 Write a two-stage `Containerfile` (uv builder on python3.13 with `uv sync --frozen --no-dev`, then `python:3.13-slim` runtime, non-root user, healthcheck on `/healthz`). Verify that `podman build` succeeds on arm64 with `google-re2` installed from its cp313 manylinux aarch64 wheel
- [x] 12.2 Write `compose.yaml` (read-only `/rules` and `/config` mounts, writable events volume, podman secrets for upstream keys, port 8080). Verify that `podman compose up` starts and `curl http://localhost:8080/healthz` returns 200
- [x] 12.3 Document running the container, SIGHUP reload (`podman kill -s HUP`), and the macOS file-watch caveat in `README.md`. Verify that the documented commands work

## 13. End-to-end integration (manual acceptance; requires a real lab upstream, not automated)

- [x] 13.1 Against a real lab upstream in monitor mode: send benign and injection prompts via both an OpenAI SDK and an Anthropic SDK client (`uv run` scripts in `scripts/`), and verify that the responses pass through and `events.jsonl` contains the expected verdicts with `enforced: false`
- [x] 13.2 Switch to enforce mode and repeat (streaming and non-streaming). Verify that the injection prompt gets a 403 whose `X-Gateway-Event-Id` matches an event, a canary leak in a response is blocked, and the clean streams replay intact
- [x] 13.3 Verify that the upstream that requires an API key receives it, the keyless upstream receives no auth header, and neither upstream ever sees a client token (inspect upstream access logs or a capture)

## 14. Validation fixes (post-`opsx-validate`, 2026-11-14 report `validations/2026-11-14T13-29-qwen3.8-27b.md`)

- [x] 14.1 VD-1: `uninspected_policy: fail_closed` with `stream: true` in monitor mode now routes through the buffered (enforce-style) path, so uninspected streams are actually blocked instead of fully relayed (design D12: fail_closed blocks regardless of mode). Verified by `tests/test_streaming.py::test_monitor_fail_closed_stream_uninspected_is_blocked` and `::test_monitor_fail_closed_stream_clean_replays_byte_identical`
- [x] 14.2 VD-2: fix `for call_index in enumerate(...)` in `formats/openai.py` — the tool-call gap location used the raw tool-call dict as the index, leaking raw arguments into events. Now `messages[i].tool_calls[j]` positional. Verified by `tests/test_formats.py::test_tool_call_gap_locations_are_positional`
- [x] 14.3 VD-3: mid-stream upstream failures map to 504 (`TimeoutException`) / 502 (other `HTTPError`) in enforce mode instead of an unhandled exception; the monitor tee (bytes already relayed) records `uninspected_content` (`stream_error`) + `request_completed` with `outcome: upstream_error`. Body-read failures (`aread()`) map the same way. Verified by `tests/test_streaming.py::test_enforce_stream_upstream_timeout_maps_to_504`, `::test_enforce_stream_upstream_error_maps_to_502`, `::test_monitor_stream_upstream_error_records_and_stops`
- [x] 14.4 VD-4: legacy `function_call` (request and response) and request `functions` arrays are now recorded as `tool_call`/`tool_definitions` uninspected gaps. Verified by `tests/test_formats.py::test_legacy_function_call_and_functions_are_gaps`
- [x] 14.5 VD-5: add the `llmgw` console entry point (`llmgw.server:main`) that loads the config and serves on `listen.host`/`listen.port`, so the `listen` config key takes effect; `Containerfile` CMD is now `["llmgw"]`; README documents `uv run llmgw`. Verified: missing config file exits 2 with a clean error; a valid config serves on the configured bind
- [x] 14.6 VD-6: enforce-mode buffer overflow no longer retains every chunk — the replay copy is dropped at the cap, bounding memory to `max_buffer_bytes` (the response is blocked on overflow). Behavior verified by `tests/test_streaming.py::test_enforce_stream_overflow_blocked`
- [x] 14.7 VD-7: add the missing `x-api-key` upstream credential injection test (e2e + unit). Verified by `tests/test_routing.py::test_x_api_key_upstream_credential_injected` and the extended `::test_credential_injection_unit`
- [x] 14.8 VD-9: an unparseable upstream response body is now flagged as `uninspected_content` (`response.body`) instead of silently skipped, so `fail_closed` sees it. Verified by `tests/test_proxy.py::test_unparseable_upstream_body_flagged` and `::test_unparseable_upstream_body_blocks_under_fail_closed`
- [x] 14.9 VD-11: malformed containers (non-list `messages`/`tools`/`choices`/`content`, non-dict choice/message, non-string/list `system`) now produce `other`/`unknown_part` gaps instead of being silently ignored or crashing. Verified by `tests/test_formats.py::test_malformed_containers_are_gaps_not_crashes`

After section 14 fixes: `uv run pytest` 187 passed, `uv run ruff check` clean, all 25 verbatim spec block commands pass, and the baseline replay is unchanged (ruleset `224951a6d389`, 16 active rules, 29 entries, TP 17/FN 0/FP 0/TN 12).

## 15. Validation fixes (post-`opsx-validate`, 2026-11-14 report `validations/2026-11-14T13-49-deepseek-v4.1-flash-1017.md`)

- [x] 15.1 VD-1: remaining silent uninspected paths now produce gaps: OpenAI non-string/non-list response `content`, non-string SSE `delta.content`, non-list `tool_calls` (no crash), non-list `functions`, and Anthropic non-list `tools`. Verified by `tests/test_formats.py::test_malformed_containers_are_gaps_not_crashes`, `::test_sse_non_text_deltas_are_gaps`, and `::test_gap_details_never_contain_raw_values`
- [x] 15.2 VD-2: `Gap.detail` no longer interpolates attacker-controlled values (unknown part type, role, block type, delta type/fields, SSE event); details are fixed descriptions. Verified by `tests/test_formats.py::test_gap_details_never_contain_raw_values`
- [x] 15.3 VD-3: an unreadable rule file is rejected with a `rule_rejected` event (file, line 0, reason) instead of raising `PermissionError`; readable packs still load and the watcher no longer dies. Verified by `tests/test_rules_loader.py::test_unreadable_file_rejected_but_others_load`
- [x] 15.4 VD-4: live-mode skipped entries are excluded from every metric; `ReplayReport` gains an `evaluated` count and the table/labels say so. Verified by `tests/test_replay.py::test_live_mode_correlates_by_request_id_in_monitor_mode` (a skipped malicious response entry is no longer an FN)
- [x] 15.5 VD-5: tests added for task 14.8's parseable/unparseable response path. Verified by `tests/test_proxy.py::test_unparseable_upstream_body_flagged` and `::test_unparseable_upstream_body_blocks_under_fail_closed`
- [x] 15.6 VD-8: duplicate-id detection now counts ids from schema-invalid lines too, so a schema-invalid duplicate disqualifies its valid twin. Verified by `tests/test_rules_loader.py::test_duplicate_id_with_schema_invalid_twin_rejects_valid_one`
- [x] 15.7 VD-9: `assert_no_secrets_in_events`, `ENVELOPE_FIELDS`, and `CANONICAL_FIELDS` are now used by tests instead of being dead code. Verified by `tests/test_proxy.py::test_upstream_credential_absent_from_responses_and_events`, `tests/test_events.py::test_envelope_fields_present_and_request_id_shared`, and `tests/test_canonical.py::test_canonical_fields_match_the_contract`
- [x] 15.8 VD-10: `Gateway` now holds one pooled `httpx.AsyncClient` per upstream (design D2), created/closed in the lifespan; the proxy no longer creates and closes a client per request. Exercised by the full proxy/streaming suite
- [x] 15.9 VD-11: SIGHUP is registered with `loop.add_signal_handler` (design D8) with a `signal.signal` fallback, and removed on stop. Exercised by the app lifespan tests
- [x] 15.10 VD-15: an empty `secret_file` now fails startup instead of yielding an empty upstream credential. Verified by `tests/test_config.py::test_empty_secret_file_fails_startup`
- [x] 15.11 VD-16: the Containerfile and compose healthchecks read `listen.port` from `/config/gateway.yaml` (default 8080) instead of hardcoding it
- [x] 15.12 VD-17: the monitor-stream latency test uses larger timing margins to reduce CI flakiness. Verified by `tests/test_streaming.py::test_monitor_stream_relays_first_chunk_before_upstream_finishes`
- [x] 15.13 VD-7: added `tests/test_server.py` covering the `llmgw` missing-config exit 2 and the configured `listen.host`/`listen.port` bind, automating task 14.5's manual verification
- [x] 15.14 VD-12/VD-13/VD-14 reviewed and left as documented observations: non-streaming response bodies remain uncapped (only request/stream caps are specified), the `request_completed` enum has no local-error value, and `secrets/vllm-key.txt` remains a placeholder operators must replace

After section 15 fixes: `uv run pytest` 196 passed, `uv run ruff check` clean, all 96 declared spec test nodes exist and pass, and the baseline replay is unchanged (ruleset `224951a6d389`, 16 active rules, 29 entries, evaluated 29, TP 17/FN 0/FP 0/TN 12).

## 16. Validation fixes (post-`opsx-validate`, 2026-11-14 report `validations/2026-11-14T13-58-qwen3.8-27b.md`)

- [x] 16.1 VD-5: remove dead code — the unused `GAP_STREAM_EVENT` constant (`formats/canonical.py`) and the unused `routing.extract_model` helper (no callers in `src/` or `tests/`). Verified: `uv run pytest` 196 passed, `uv run ruff check` clean.

The remaining findings in that report were reviewed and left as documented observations: VD-1 (non-streaming response bodies uncapped — only request/stream caps are spec'd) and VD-2 (no local-error value in the `request_completed` outcome enum — the enum is spec-fixed) were deliberately retained per 15.14; VD-3 (PL-001 test-canary dependency) is the behavior the spec and design D11 prescribe; VD-4 (placeholder `secrets/vllm-key.txt`) is an intentional lab placeholder; VD-6 (Containerfile builder image) deviates from D13 but was verified by a real `podman build` (12.1); VD-7/VD-8/VD-9 are validator-constraint observations (25-command cap, no live lab upstream or podman re-run available to the validator).
