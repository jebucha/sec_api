## Audit Report: add-llm-security-gateway

**Schema:** spec-driven
**Model:** claude-sonnet (claude-sonnet-5-5)
**Artifacts checked:** proposal.md, design.md, tasks.md, specs/client-auth/spec.md, specs/content-normalization/spec.md, specs/detection-engine/spec.md, specs/detection-rules/spec.md, specs/detection-testing/spec.md, specs/llm-proxy/spec.md, specs/security-events/spec.md
**Codebase state:** greenfield. The only non-OpenSpec file is `pyproject.toml` (name `sec-api`, `requires-python = ">=3.15"`, `dependencies = []`). Pattern-consistency checks (7c) are not applicable.
**Prior audits:** `audits/2026-11-13T18-07-claude-fable.md` (no `## Resolved` header, so no regression detection). No artifact has been modified since that audit (all mtimes 17:57-18:01), so this is an independent second opinion on the same artifacts.

### Summary
- Errors: 1
- Warnings: 22
- Info: 15

---

### Findings

| ID | Category | Severity | Description | Evidence |
|----|----------|----------|-------------|----------|
| AC-1 | Accuracy | Error | Live replay correlates on `X-Gateway-Event-Id`, but the proxy spec emits that header only on 403 block responses, so the monitor-mode live scenario cannot be satisfied | `specs/detection-testing/spec.md` L26-28 ("Live run through monitor-mode gateway … `X-Gateway-Event-Id`-correlated events"); `specs/llm-proxy/spec.md` L100-101 (header only on block responses); task 11.2 ("correlates via `X-Gateway-Event-Id`"). Monitor mode blocks nothing, so no response carries the header. Fix: require a correlation header (e.g. `X-Gateway-Request-Id`) on every proxied response, or redefine live-mode correlation. |
| AC-2 | Accuracy | Warning | Rule schema omits the type-specific payload fields | `specs/detection-rules/spec.md` L17 lists required/optional fields but never names the regex pattern, keyword list, `heuristic` name, `params`, or case-sensitivity. Design D6/D7 and task 5.1 ("heuristic `params`") assume them. |
| AC-3 | Accuracy | Warning | Duplicate-JSON-key 400 rejection exists in design/tasks but in no spec | `design.md` L121; tasks 4.2, 4.3. No requirement or scenario in `content-normalization` or `llm-proxy`. |
| AC-4 | Accuracy | Warning | Enforce-mode `max_buffer_bytes` overflow is designed and tasked but absent from the specs | `design.md` D9 L77; task 8.2. `specs/llm-proxy/spec.md` L87-98 has no overflow scenario; `detection-engine` "Detection errors fail per mode" (L70-76) covers rule exceptions only. |
| AC-5 | Accuracy | Info | Near-duplicate credential non-disclosure requirements | `specs/llm-proxy/spec.md` L75-77 vs `specs/security-events/spec.md` L44-45 ("Secrets never logged"). |
| AC-6 | Accuracy | Info | Duplicate scenario: block-response/event-id correlation | `specs/llm-proxy/spec.md` L103-105 vs `specs/security-events/spec.md` L26-28. |
| AC-7 | Accuracy | Info | Canary env var is configurable in design but hard-coded in tasks | `design.md` L92 `canary_tokens_env: LLMGW_CANARIES` vs task 5.4 "reading `LLMGW_CANARIES`". |
| AC-8 | Accuracy | Info | Tasks write to directories absent from the module layout | `docs/` (tasks 5.7, 11.3, 11.4) and `scripts/` (task 13.1) not in `design.md` D3 L31-55. |
| CM-1 | Completeness | Warning | "Disabled rules" requirement has no covering task | `specs/detection-rules/spec.md` L56-61. No task mentions `enabled`; task 5.5 says only "active rules". |
| CM-2 | Completeness | Warning | No task emits the `uninspected_content` event | `specs/content-normalization/spec.md` L46. Tasks 4.2/4.3 only record gaps in the canonical structure; task 7.3 covers only `fail_closed`. |
| CM-3 | Completeness | Warning | No task builds the `verdict` event with its required fields | `specs/security-events/spec.md` L23-24 (stage, format, model, upstream, action, `enforced`, matches). Tasks 6.1-6.3 produce a `Verdict`; 7.3/8.1 say only "events run". |
| CM-4 | Completeness | Info | Startup with invalid rules / unreadable rules dir is not tasked | `specs/detection-rules/spec.md` L81-86. Task 7.2 loads rules in lifespan; task 9.1 tests only reload. |
| CM-5 | Completeness | Info | 9 tasks map to no spec requirement | Tasks 1.1, 1.2, 1.3, 3.4, 5.7, 11.4, 12.1, 12.2, 12.3 (scaffolding, docs, container; expected). |
| VL-1 | Validity | Warning | No `client-auth` scenario carries a Test Requirement block (7 scenarios) | `specs/client-auth/spec.md`, all `#### Scenario:` entries; all behavior-changing. |
| VL-2 | Validity | Warning | No `content-normalization` scenario carries a Test Requirement block (10 scenarios) | `specs/content-normalization/spec.md`. |
| VL-3 | Validity | Warning | No `detection-engine` scenario carries a Test Requirement block (10 scenarios) | `specs/detection-engine/spec.md`. |
| VL-4 | Validity | Warning | No `detection-rules` scenario carries a Test Requirement block (15 scenarios) | `specs/detection-rules/spec.md`. |
| VL-5 | Validity | Warning | No `detection-testing` scenario carries a Test Requirement block (7 scenarios) | `specs/detection-testing/spec.md`. |
| VL-6 | Validity | Warning | No `llm-proxy` scenario carries a Test Requirement block (21 scenarios) | `specs/llm-proxy/spec.md`. |
| VL-7 | Validity | Warning | No `security-events` scenario carries a Test Requirement block (6 scenarios) | `specs/security-events/spec.md`. |
| VL-8 | Validity | Warning | Detection metrics are not defined precisely enough to test | `specs/detection-testing/spec.md` L31: no formula for detection rate / FP rate; per-rule FN/TN undefined for malicious entries without `expected_rules`. Task 11.1 ("known expected counts") depends on this. |
| VL-9 | Validity | Warning | Offline replay does not say which stage an entry is evaluated at; `format` is meaningless offline | `specs/detection-testing/spec.md` L10, L16-21. `field: assistant_text` could be request-stage (history) or response-stage; the `prompt-leak` rule is response-stage. |
| VL-10 | Validity | Warning | "Time linear in the input length" is not a testable criterion | `specs/detection-rules/spec.md` L35-36. Task 5.6 has the concrete bound (100k chars, under 1s); the spec should state it. |
| VL-11 | Validity | Info | Vague qualifier "minimal status body" | `specs/client-auth/spec.md` L51. State the body shape. |
| VL-12 | Validity | Info | "Constant time" comparison is not black-box observable | `specs/client-auth/spec.md` L32; verifiable only by inspection (`hmac.compare_digest`, design D3). |
| VL-13 | Validity | Info | `design.md` has no `## Test Strategy` section | D14 "Testing" (L113-114) covers runner and fakes but is not the expected header. |
| FS-1 | Feasibility | Warning | Task 1.1 and design D1 conflict with the existing `pyproject.toml` | Existing file has `requires-python = ">=3.15"`; D1 and tasks 1.1/12.1 target Python 3.13. `uv init` refuses to run in an initialized project, and `uv sync` under 3.13 would violate `requires-python`. Pick a version and change 1.1 to edit the existing file. |
| FS-2 | Feasibility | Warning | `uv run pytest` with zero tests exits 5, not 0 | Task 1.3 ("both exit 0"). pytest returns 5 when no tests are collected. |
| FS-3 | Feasibility | Info | New dependencies introduced | Task 1.2 (starlette, uvicorn, httpx, google-re2, jsonschema, pyyaml, watchfiles, python-ulid; dev: pytest, pytest-asyncio, ruff). Proposal "Impact" omits pyyaml and python-ulid. `google-re2` arm64 wheel availability is an acknowledged risk (design L122). |
| FS-4 | Feasibility | Info | Section 13 requires a real lab upstream and cannot be automated | Tasks 13.1-13.3. Fine as manual acceptance steps; label them so. |
| CO-1 | Coherence | Warning | Task 7.3 bundles about eight behaviors into one task | `tasks.md` 7.3: byte passthrough, both eval stages, 403 shape, opaque/debug messages, `fail_closed`, 502/504, `request_completed`. Split into 3-4 tasks. |
| CO-2 | Coherence | Warning | Prompt-leak corpus entry depends on a canary that is deliberately not committed | `specs/detection-testing/spec.md` L51-53 requires a TP per category; `design.md` D11 keeps canaries in env. Tasks 10.3/11.3 don't say how corpus and replay agree on a test canary. |
| CO-3 | Coherence | Warning | Security: no request body size limit | `design.md` D2 reads `Request.body()` in full; `max_buffer_bytes` (D9) covers only enforce-mode responses. Add `max_request_bytes` with 413 handling. |
| CO-4 | Coherence | Warning | Security: monitor-mode stream buffer is unbounded | `design.md` D9 L75 ("appends it to a buffer", no cap); risk entry L125 mentions enforce mode only. |
| CO-5 | Coherence | Info | Security: hop-by-hop and `Host` header policy unspecified | `design.md` D3 `routing.py`; `specs/llm-proxy/spec.md` L55-57 name only `Authorization`/`x-api-key`. |
| CO-6 | Coherence | Info | Security: request `tools[]` definitions are not recorded as uninspected | `specs/content-normalization/spec.md` L46 omits OpenAI `tools[].function.description` / Anthropic `tools[].description`. Deferred by proposal L20, but the gap should be flagged. |
| CO-7 | Coherence | Info | `request_completed` scope is ambiguous for auth failures | `specs/security-events/spec.md` L31 lists `auth_failed` as an outcome, while `client-auth` rejects before parsing. State whether both events are emitted. |
| CO-8 | Coherence | Info | Task 5.2 depends on the heuristic registry created in task 5.4 | Task 5.2 ("resolve heuristics from the registry", unknown-heuristic test) needs `rules/heuristics.py` from 5.4. Reorder or stub. |

### Coverage Metrics

| Metric | Value |
|--------|-------|
| Total requirements | 52 |
| Total scenarios | 76 |
| Total tasks | 44 |
| Requirements with ≥1 task | 49 (94%) |
| Tasks with mapped requirement | 35 (80%) |
| Duplications found | 2 |
| Ambiguities found | 5 |
| Requirements with executable test requirements | 0 (0%) |

---

### Verdict
Cannot proceed to implementation. Resolve errors first.

The single error (AC-1) is a small spec fix: give every proxied response a correlation header so live replay works in monitor mode. The warnings cluster into four groups: (1) Test Requirement blocks are absent everywhere (VL-1..7, VL-13); (2) behaviors in design/tasks but not specs (AC-2..4) and spec behaviors with no task (CM-1..3); (3) underspecified replay metrics and stage semantics (VL-8, VL-9, CO-2); (4) scaffolding conflicts with the existing `pyproject.toml` (FS-1, FS-2) and two missing memory bounds (CO-3, CO-4).
