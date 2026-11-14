# Design

## Context

Greenfield repository. The only existing structure is OpenSpec tooling. The target environment is a home lab LAN with self-hosted inference servers (llama.cpp, LM Studio, vLLM) exposing OpenAI- and/or Anthropic-compatible APIs. Some of those servers require an API key and some do not. The host has `podman` (not docker) and `uv`. All traffic is plain HTTP inside the LAN. See proposal.md for motivation and scope, and `specs/` for the behavior contract.

## Goals / Non-Goals

**Goals:**
- One small, readable Python service that a security engineer can study and extend. Clarity beats throughput.
- Detection behavior is identical in the gateway and in the offline replay tool (same loader, same engine).
- Every detection decision is reproducible from events: ruleset hash + request id + excerpts.
- Leave seams for the deferred features (tool inspection, sliding-window streaming, classifier/judge tiers) without building them.

**Non-Goals:**
- High throughput or horizontal scaling. Single process, single container.
- TLS termination, an HTTP admin API, multi-tenant policy.
- Persisting request/response bodies.

## Decisions

### D1. Python 3.13 managed entirely with uv
The repo already has a `pyproject.toml` (project `sec-api`, `requires-python = ">=3.15"`, no dependencies). Edit it in place rather than running `uv init`. Keep the project name `sec-api`, set `requires-python = ">=3.13,<3.15"`, pin `.python-version` to `3.13`, and add a `uv_build` backend with `module-name = "llmgw"` so the importable package is `src/llmgw/`. Python 3.13 is chosen because `google-re2` (1.1.20251105) publishes wheels for cp313/cp314 on macOS arm64 and manylinux aarch64, but not for 3.15. Use `uv sync` for environments, `uv run` for every command (server, tests, replay CLI), and `uv add` for dependencies. The container build uses the official uv image pattern (`uv sync --frozen --no-dev`). No pip, requirements.txt, or bare venv.
*Alternatives:* Poetry, pip-tools. uv is the user's standard and is fastest. Python 3.15: requires building RE2 from source on host and in the container.

### D2. Starlette + uvicorn + httpx, not FastAPI
The gateway needs raw body bytes (forwarded unmodified, see llm-proxy spec) and hand-built streaming responses. FastAPI's request-model validation would parse and re-serialize bodies and get in the way. Starlette gives direct `Request.body()` and `StreamingResponse`. `httpx.AsyncClient` handles upstream calls, including streaming (`client.stream`). One shared client per upstream keeps connections pooled.
*Alternatives:* FastAPI (validation overhead, encourages re-serialization), aiohttp (fine, less familiar ecosystem).

### D3. Module layout
```
src/llmgw/
  app.py            Starlette app, routes, lifespan (load config/rules, start watcher)
  config.py         YAML config -> typed dataclasses; secret resolution from env/files
  auth.py           token extraction (Bearer | x-api-key), sha256 + hmac.compare_digest
  routing.py        model -> upstream lookup, header forwarding policy (D4a), credential stripping/injection
  proxy.py          forward, stream tee (monitor), stream buffer+replay (enforce)
  formats/
    canonical.py    Canonical dataclass
    openai.py       request/response/SSE parsing + error shapes
    anthropic.py    request/response/SSE parsing + error shapes
  rules/
    schema.json     JSON Schema for rule lines (published, versioned)
    loader.py       parse JSONL -> validate -> compile -> self-test -> Ruleset
    ruleset.py      immutable Ruleset + content hash
    heuristics.py   built-in heuristics registry
    watcher.py      watchfiles + SIGHUP -> reload -> atomic swap
  engine.py         evaluate(Ruleset, Canonical, stage) -> Verdict
  events.py         envelope, ULID ids, JSONL writer (file + optional stdout)
  replay/cli.py     corpus loader, offline/live modes, metrics, thresholds
rules/              starter packs (*.jsonl), mounted into container
corpus/             starter corpus (*.jsonl)
config/             example gateway.yaml
docs/               rules.md, testing.md, baseline.md
scripts/            manual end-to-end client scripts (section 13 tasks)
tests/
```

### D4. Format adapters behind a small protocol
Each wire format provides: `parse_request(bytes) -> Canonical`, `parse_response(bytes) -> Canonical`, `iter_sse_text(chunks) -> str deltas`, and `error_body(status, type, message) -> bytes`. The route decides the format: `/v1/chat/completions` means openai, `/v1/messages` means anthropic. Upstreams are assumed to speak the same format as the inbound endpoint. No cross-format translation happens because bytes pass through unchanged.
*Consequence:* An upstream must support the endpoint the client used. The routing map doesn't enforce this. A mismatch surfaces as an upstream 404, relayed as-is.

### D4a. Header forwarding policy
Request to upstream: forward the client's end-to-end headers except `Authorization` and `x-api-key` (stripped, then replaced per the upstream's auth style), the hop-by-hop headers (`Connection`, `Keep-Alive`, `Proxy-Authenticate`, `Proxy-Authorization`, `TE`, `Trailer`, `Transfer-Encoding`, `Upgrade`, plus any named in `Connection`), `Host` (httpx sets it from the upstream URL), and `Content-Length` (httpx recomputes it from the unchanged bytes). `Accept-Encoding` is forced to `identity`, so the bytes the gateway inspects are exactly the bytes it relays. Response to client: forward the upstream status and end-to-end headers, drop hop-by-hop headers and `Content-Length` (Starlette recomputes it), and add `X-Gateway-Request-Id`.

### D4b. Request size limit
Before parsing, `app.py` checks `Content-Length` and the bytes actually read against `max_request_bytes` (default 8 MiB). An oversized body gets a provider-shaped 413 (llm-proxy: Request size limit). The read stops at the cap, so an oversized body is never held in memory in full.

### D5. Canonical structure is inspection-only
`Canonical(system: list[str], user_text: list[str], assistant_text: list[str], tool_calls: list, tool_results: list, uninspected: list[Gap])`. Fields are lists of segments, not concatenated strings, so match excerpts and offsets refer to one message, and a pattern can't accidentally match across a message boundary. `tool_calls`/`tool_results` exist but stay empty in v1. Their content goes to `uninspected`. This is the seam for the tool-inspection change.

### D6. RE2 via `google-re2`
All `regex` rules compile with `re2`. Patterns with backreferences or lookarounds fail compilation, so the rule is rejected. Keyword rules compile to a single RE2 alternation with escaped literals, case-insensitive by default (`case_sensitive: true` overrides). This removes ReDoS risk from the rule-pack attack surface entirely, rather than relying on timeouts.
*Alternatives:* the `regex` module with per-match timeout (keeps lookarounds, but a timeout is a mitigation, not a guarantee, and it still burns CPU up to the timeout).

### D7. Heuristics are a fixed, code-defined registry
`heuristic` rules reference a name (e.g. `luhn_card`, `unicode_tags`, `zero_width`, `base64_run`) plus `params`. The registry is code, so rule files can't introduce executable logic. Heuristics are pure functions over a segment, returning match spans.

### D8. Ruleset is immutable and swapped by reference
The loader builds a complete `Ruleset` (frozen, with a sha256 over the canonical JSON of active rules sorted by id). Reload replaces a single module-level reference. Each request captures the reference once at its start, so in-flight requests keep their ruleset (detection-rules spec: Hot reload). Swapping a Python reference is atomic. No locks are needed for readers. The `watchfiles` watcher debounces bursts (editors write multiple times), and SIGHUP goes through `loop.add_signal_handler`.

### D9. Streaming strategy chosen by global mode
- **monitor:** `StreamingResponse` over an async generator that yields each upstream chunk immediately and appends it to a buffer. After the final chunk, response-stage evaluation runs and events are written. Latency is unaffected. Under `uninspected_policy: fail_closed` the enforce-style buffered path is used instead: a stream already relayed cannot be blocked, so fail_closed buffers, evaluates, then relays or blocks (D12: fail_closed blocks even in monitor mode).
- **enforce:** read the full upstream stream into memory, parse the SSE text deltas, evaluate, then either replay the exact original chunks as a `StreamingResponse` or return a 403 block. The client sees no bytes until evaluation finishes.
- A configurable `max_buffer_bytes` (default 4 MiB) caps buffering in both modes. In enforce mode, exceeding it is a detection error: the response is blocked and a `rule_error` event records the overflow (llm-proxy: Streaming in enforce mode). In monitor mode the generator keeps relaying chunks but stops appending to the buffer. It evaluates only the accumulated prefix and emits an `uninspected_content` event of type `stream_overflow` (llm-proxy: Streaming in monitor mode).
SSE parsing only needs to pull out text deltas (`choices[].delta.content` for OpenAI, `content_block_delta.delta.text` for Anthropic). Other event types go into `uninspected`.

### D10. Configuration
A single YAML file (`gateway.yaml`), loaded at startup only. Upstreams, models, clients, and mode need a restart. Only rules hot-reload.
```yaml
listen: { host: 0.0.0.0, port: 8080 }
mode: monitor                 # monitor | enforce
uninspected_policy: flag      # flag | fail_closed
debug_verdicts: false
upstream_timeout_s: 120
max_buffer_bytes: 4194304
max_request_bytes: 8388608
excerpt_max_chars: 200
rules_dir: /rules
events: { path: /var/log/llmgw/events.jsonl, stdout: true }
canary_tokens_env: LLMGW_CANARIES   # comma-separated, consumed by the prompt-leak heuristic
upstreams:
  gpu-box:  { url: http://10.0.0.20:8000, auth: { style: bearer, secret_env: VLLM_KEY } }
  mac-mini: { url: http://10.0.0.21:1234, auth: { style: none } }
models:
  qwen3-32b: gpu-box
  llama-3.1-8b: mac-mini
clients:
  opencode-laptop: { token_sha256: "<hex>" }
```
Secrets resolve from `secret_env` or `secret_file` (podman secrets mount at `/run/secrets/*`). If a referenced secret is missing, the gateway fails at startup rather than at first request. A small `uv run llmgw-token <name>` helper generates a random token and prints the plaintext once, along with its sha256 for the config.

### D11. Canary detection is a heuristic, not a literal regex
The prompt-leak rule is `type: heuristic, heuristic: canary`. It reads canary values at load time from the environment variable named by `canary_tokens_env`, so the canaries themselves never live in the committed rule pack. Otherwise anyone reading the rules repo would know the canaries. The replay tool's `--canaries` option supplies the values directly to the heuristic for that run. The starter corpus uses one documented test-only value (`LLMGW-TEST-CANARY-0000`, recorded in `docs/testing.md`) that must never be configured in a real deployment. Consequence: the shipped prompt-leak rule PL-001 pins that same value in its inline self-test, so a deployment whose canary set omits it sees PL-001 rejected at load (a loud `rule_rejected`, the rest of the ruleset unaffected); such a deployment ships a local PL-001 whose self-test uses its own canary, or disables the rule. The lab `compose.yaml` includes the test canary in `LLMGW_CANARIES` for exactly this reason.

### D12. Events
ULIDs (time-sortable) for `event_id` and `request_id`. The `request_id` is generated when a request arrives, before auth, and returned on every API response — including 401 authentication failures — as `X-Gateway-Request-Id`. The live replay tool correlates on it. `X-Gateway-Event-Id` is added only on block responses and names the `verdict` event. An auth failure emits `auth_failed` followed by `request_completed` (outcome `auth_failed`). Events are written with a single `write()` of one line plus `\n` per event to a file opened in append mode. Excerpt masking (`mask_excerpt`) happens in the engine before events are built, so unmasked values never reach the event layer. `uninspected_policy: fail_closed` blocks even in monitor mode. It's an explicit operator choice to close inspection gaps, independent of trialing rules.

### D13. Container
`Containerfile` (also works with `docker build`) in two stages, both on `ghcr.io/astral-sh/uv:python3.13-trixie-slim` (Python 3.13 on Debian trixie slim, `uv` preinstalled — no `pip install uv` step). It runs as a non-root user, with `/rules` and `/config` mounted read-only and `/var/log/llmgw` writable. `compose.yaml` works with `podman compose`. Healthcheck: `GET /healthz`. The image-level `HEALTHCHECK` only survives a docker-format build (`podman build --format docker`); podman's default OCI format drops it with a warning, so `compose.yaml` repeats the probe at service level. Rules are mounted from the host, so editing them on the host triggers hot reload. Note: inotify events do cross bind mounts on Linux. On macOS podman machine, virtiofs may not propagate them, so SIGHUP (`podman kill -s HUP`) is the documented fallback.

### D14. Testing
`pytest` + `pytest-asyncio`, run via `uv run pytest`. Upstreams are faked with `httpx.MockTransport` or a tiny Starlette fake. A parity test suite sends the same payloads through both formats and asserts identical canonical output and verdicts. That's the translation-bypass check from exploration. See `## Test Strategy` for the runner contract used by spec Test Requirement blocks.

## Risks / Trade-offs

- **Enforce-mode buffering kills perceived streaming on slow local models** → Documented trade-off. Monitor mode stays zero-latency. Sliding-window streaming is a planned follow-up change.
- **RE2 rejects lookarounds, which some public injection signatures use** → Rewrite them as separate keyword/regex rules or a heuristic. Rejection is loud (`rule_rejected`), never silent.
- **Regex/keyword detection is trivially evadable (paraphrase, translation, encoding)** → This is expected and part of the learning goal. The corpus and replay tool make evasion measurable. Classifier and judge tiers are deferred changes.
- **Byte passthrough means the canonical parser and the upstream could interpret a request differently (e.g. duplicate JSON keys, odd encodings)** → The parser rejects requests with duplicate keys at the top level and in messages (400, content-normalization: Duplicate JSON keys rejected). The parity tests cover known differences. Any remaining differential is documented as a known research area.
- **`google-re2` wheel availability on the linux container target (arm64/amd64)** → Verify in the first container build task. Fallback is building from source in the builder stage (requires `libre2-dev`, `pybind11`).
- **File watching may not fire through podman machine mounts on macOS** → SIGHUP fallback (D13).
- **Plain HTTP on the LAN exposes client tokens to anyone sniffing the LAN** → Accepted for a lab per proposal. Tokens are per-client and revocable by removing the hash plus a restart.
- **Large stream buffers or request bodies held in memory** → `max_buffer_bytes` caps stream buffering in both modes (D9), and `max_request_bytes` caps request bodies (D4b). Single-user lab load.
- **Request `tools[]` definitions are an injection surface left uninspected in v1** → Recorded as `uninspected` (type `tool_definitions`) with an `uninspected_content` event, so the gap is visible and `fail_closed` can block it. Inspection is deferred to the tool-inspection change.

## Test Strategy

- **Runner:** pytest with pytest-asyncio (`asyncio_mode = "auto"`), configured in `pyproject.toml`.
- **Full-suite command:** `uv run pytest`
- **Block commands:** each spec Test Requirement block runs one test node: `uv run pytest <file>::<test>`. Pass criterion: exit code 0, no skipped tests.
- **Fakes:** upstreams use `httpx.MockTransport` or an in-process Starlette fake. The gateway app is exercised in-process via Starlette `TestClient`/`httpx.ASGITransport`. Units under test are never mocked. Only their dependencies are (upstream network, clock where needed).
- **Not black-box testable:** constant-time token comparison (client-auth: Hashed token storage) is verified by code inspection that `auth.py` uses `hmac.compare_digest`. No timing test is attempted.
- **Manual only:** section 13 tasks require a real lab upstream and are not covered by Test Requirement blocks.
- **Prerequisites:**
  - `cmd: uv --version`
  - `file: pyproject.toml`
  - `cmd: uv run python -c "import re2"`

## Migration Plan

Greenfield, so there is nothing to migrate. Rollout:
1. Run in `monitor` mode and point one client (e.g. a curl script, then opencode) at the gateway.
2. Replay the corpus and watch `events.jsonl` to tune rules.
3. Switch `mode: enforce` and restart.
Rollback: point clients back at the upstreams directly.

## Open Questions

- Exact upstream ports and paths per server in the lab. These are config values only and don't affect the design.
- Whether to ship events to a SIEM later. The JSONL format is SIEM-ready either way.
