# Proposal

## Why

Self-hosted LLM inference servers in the lab (llama.cpp, LM Studio, vLLM) are reached directly, with nothing inspecting what goes in or comes out. A containerized gateway in front of them gives one place to detect and block prompt injection, jailbreaks, secret/PII leakage, and system-prompt leaks. The detection logic is machine-readable JSONL, so it can be versioned, tested, and measured like any other detection content. This also serves as a hands-on platform for learning AI security against the OWASP LLM Top 10.

## What Changes

- New containerized Python service that proxies chat traffic for both OpenAI-compatible (`/v1/chat/completions`, `/v1/models`) and Anthropic-compatible (`/v1/messages`) APIs over plain HTTP on the lab LAN.
- Per-client bearer-token authentication (accepting both `Authorization: Bearer` and `x-api-key`). Tokens are stored as SHA-256 hashes. Client credentials are always stripped before forwarding.
- Model-name → upstream routing map. Each upstream has optional credential injection from environment/secret files. Unknown models fail closed.
- Normalization of both wire formats into one canonical structure for inspection. The original request bytes are forwarded unchanged. Content the parser does not recognize is flagged as uninspected (fail-closed optional).
- Detection rules as JSONL rule packs. Each rule is JSON Schema validated, compiled with RE2 (linear-time, ReDoS-safe), and self-tested via inline match/no-match examples at load time. Rules hot-reload with an atomic swap on file change or SIGHUP.
- Per-rule actions (`block`, `flag`, `log`) plus a global `monitor`/`enforce` mode. Streaming responses are teed and inspected asynchronously in monitor mode, and buffered then released in enforce mode.
- Blocks return provider-shaped 4xx errors with an opaque message and an `X-Gateway-Event-Id` correlation ID. Every API response carries an `X-Gateway-Request-Id`. A `debug_verdicts` flag exposes rule details for lab testing.
- Structured JSONL security events (verdicts, uninspected content, rule load/reload results) carrying correlation IDs and the active ruleset hash.
- Starter rule packs: prompt-injection, jailbreak, secrets, PII, unicode/encoding smuggling, and a canary-token prompt-leak detector.
- A labeled JSONL test corpus and a replay CLI that report per-rule detection and false-positive rates.

Out of scope (future changes): tool-call/RAG/indirect-injection inspection, sliding-window stream inspection, ML classifier and LLM-as-judge tiers, a redact action, model aliasing, TLS, an HTTP admin API.

## Capabilities

### New Capabilities
- `llm-proxy`: Client-facing OpenAI/Anthropic-compatible endpoints, model-to-upstream routing, upstream credential injection, streaming behavior per mode, and provider-shaped block/error responses.
- `client-auth`: Authentication of lab clients to the gateway with per-client tokens and identity attribution.
- `content-normalization`: Parsing both wire formats into a canonical inspection structure, and handling of content that cannot be inspected.
- `detection-rules`: The JSONL rule schema, rule validation and self-testing, rule pack loading, hot reload, and the starter rule packs.
- `detection-engine`: Evaluation of rules against canonical content at the request and response stages, per-rule actions, and the global monitor/enforce mode.
- `security-events`: The JSONL event stream: verdict, uninspected-content, and rule-lifecycle events with correlation IDs and ruleset hashes.
- `detection-testing`: The labeled test corpus format and the replay tool that measures detection and false-positive rates.

### Modified Capabilities
_None. This is a greenfield project._

## Impact

- New codebase: Python service (async HTTP server and client), packaged as a container image with rules, config, and secrets mounted as volumes.
- New dependencies: an ASGI web framework (`starlette`, `uvicorn`), an async HTTP client (`httpx`), `google-re2`, a JSON Schema validator (`jsonschema`), a YAML parser (`pyyaml`), a ULID generator (`python-ulid`), and a file-watching library (`watchfiles`).
- The existing `pyproject.toml` (project `sec-api`) is retargeted from Python `>=3.15` to `>=3.13,<3.15`, because `google-re2` ships no 3.15 wheels.
- Network: listens on the lab LAN over HTTP. Initiates HTTP connections to configured upstream inference servers only.
- Operational: client tokens and upstream API keys become secrets managed by the gateway operator. Inference clients (e.g. opencode, Open WebUI, scripts) must be repointed at the gateway and issued tokens.
