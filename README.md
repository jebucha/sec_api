# sec-api — LLM security gateway

A containerized gateway in front of self-hosted LLM inference servers
(llama.cpp, LM Studio, vLLM) that inspects chat traffic for prompt injection,
jailbreaks, secret/PII leakage, and system-prompt leaks. It speaks both
OpenAI-compatible (`/v1/chat/completions`, `/v1/models`) and
Anthropic-compatible (`/v1/messages`) APIs over plain HTTP on the lab LAN.

- Detection rules are machine-readable JSONL, validated against a published
  schema, compiled with RE2 (linear-time), self-tested at load, and hot-reloaded.
- `monitor` mode inspects without blocking; `enforce` mode blocks.
- Every decision is recorded as a structured JSONL security event with
  correlation IDs and the active ruleset hash.
- A labeled corpus + replay CLI measures detection and false-positive rates.

See `docs/rules.md` (rule format), `docs/testing.md` (replay tool), and
`docs/baseline.md` (starter-corpus baseline) for details.

## Development

```bash
uv sync                 # create .venv (Python 3.13, managed by uv)
uv run pytest           # run the test suite
uv run ruff check       # lint
uv run llmgw gateway.yaml   # run the gateway (listens on listen.host:port)
```

## Configuration & tokens

The gateway is configured by a single YAML file (`gateway.yaml`, loaded at
startup). Start from `config/gateway.example.yaml`:

```yaml
listen: { host: 0.0.0.0, port: 8080 }
mode: monitor                 # monitor | enforce
uninspected_policy: flag      # flag | fail_closed
debug_verdicts: false
rules_dir: /rules
events:
  path: /var/log/llmgw/events.jsonl
  stdout: true
canary_tokens_env: LLMGW_CANARIES

upstreams:
  gpu-box:
    url: http://10.0.0.20:8000
    auth: { style: bearer, secret_env: VLLM_KEY }   # bearer | x-api-key | none
  mac-mini:
    url: http://10.0.0.21:1234
    auth: { style: none }

models:                       # model name -> upstream
  qwen3-32b: gpu-box

clients:
  opencode-laptop:
    token_sha256: "<sha256 hex>"
```

Keys you will change per lab: `listen` (bind address), `upstreams` (URLs +
secrets), `models` (must reference configured upstreams), `clients`,
`rules_dir`, `events`.

Upstream secrets resolve from `secret_env` (an environment variable) or
`secret_file` (e.g. a podman secret under `/run/secrets/`). If a referenced
secret is missing, the gateway **fails at startup** rather than at first
request.

### Client tokens

Client tokens are stored only as SHA-256 digests. Generate one with:

```bash
uv run llmgw-token opencode-laptop
```

The command prints the plaintext token **once** and its sha256:

```
client: opencode-laptop
token: llmgw_xxxxxxxx...
token_sha256: 3f9a...
# Store the sha256 in gateway.yaml: clients.opencode-laptop.token_sha256
```

Put the `token_sha256` in `clients`. Clients may send the token as
`Authorization: Bearer <token>` (OpenAI SDK style) or `x-api-key: <token>`
(Anthropic SDK style). To revoke a client, remove its hash and restart.

## Container deployment (podman)

The `Containerfile` is two stages: a `ghcr.io/astral-sh/uv:python3.13-trixie-slim`
builder (`uv` preinstalled) that installs the project and dependencies with
`uv` (frozen, from `uv.lock`), then a runtime on the same image that runs as
the non-root `llmgw` user. It works
with `podman build` and `docker build`:

```bash
podman build --format docker -t sec-api-gateway .
```

Use `--format docker`: podman's default OCI image format has no home for the
`HEALTHCHECK` instruction and drops it with a warning, so a container run
directly with `podman run` would not be probed. `podman compose` builds may
still print that warning, but it is harmless there — the service-level
`healthcheck` in `compose.yaml` applies the same probe.

Before bringing the stack up, edit:

| File | What to change |
| --- | --- |
| `secrets/vllm-key.txt` | Your upstream API key (single line) |
| `config/gateway.yaml` | `upstreams` URLs, `models`, client `token_sha256` |
| `compose.yaml` | `LLMGW_CANARIES` (comma-separated canary values) |

`config/gateway.yaml` is the container config: the upstream key is read from
the podman secret `/run/secrets/vllm-key` (mounted from
`secrets/vllm-key.txt`), rules from `./rules` (read-only), and events go to
`/var/log/llmgw/events.jsonl` on a named volume. A missing secret makes the
gateway **fail at startup**, not at first request.

```bash
podman compose up -d
curl -s http://localhost:8080/healthz   # -> 200
```

A healthy startup logs a `ruleset_loaded` event (also to the events file);
check `accepted` / `rejected` there:

```bash
podman exec sec-api-gateway tail -n 1 /var/log/llmgw/events.jsonl
```

### Hot reload and SIGHUP

The rules watcher reloads the ruleset when `rules/*.jsonl` files change. On a
Linux host, inotify events cross the bind mount, so saving a rule file reloads
the ruleset. On macOS, the podman machine's virtiofs mount may not deliver
inotify events — force a reload with SIGHUP instead:

```bash
podman kill -s HUP sec-api-gateway
```

### Notes

- The image carries a `HEALTHCHECK` on `GET /healthz`. Podman's default OCI
  image format ignores image-level healthchecks, so `compose.yaml` repeats it
  as a service-level `healthcheck`. To keep the image-level probe (e.g. for
  `docker run`), build with `podman build --format docker`.
- The prompt-leak rule PL-001 is a canary heuristic: its inline self-test
  references the documented test-only canary `LLMGW-TEST-CANARY-0000`, so the
  rule only loads when that canary is among `LLMGW_CANARIES` (see
  `docs/testing.md`; the starter `compose.yaml` includes it for the lab
  deployment).
- `podman compose down` keeps the events volume; remove it with
  `podman volume rm sec_api_llmgw-events`.

## Lab acceptance (scripts/)

The end-to-end acceptance checks are runnable scripts that point at your live
gateway (OpenAI SDK + Anthropic SDK clients):

```bash
# monitor mode: everything passes through; verdicts logged with enforced: false
uv run scripts/lab_monitor.py --token "$LLMGW_TOKEN" --events /path/to/events.jsonl

# enforce mode: 403 blocks with X-Gateway-Event-Id correlation, stream replay,
# response-stage canary block
uv run scripts/lab_enforce.py --token "$LLMGW_TOKEN" --events /path/to/events.jsonl \
    --canary "$MY_CANARY"

# upstream credentials + client-token isolation (capture via fake_upstream.py)
uv run python scripts/fake_upstream.py --port 9101 --require-key "$VLLM_KEY" \
    --header-log /tmp/up.jsonl &
uv run scripts/lab_upstream_keys.py --token "$LLMGW_TOKEN" \
    --upstream-key "$VLLM_KEY" --header-log /tmp/up.jsonl
```

The `lab_*.py` scripts are standalone PEP 723 scripts: `uv run` fetches the
OpenAI and Anthropic SDKs on first use, no project setup.
`scripts/fake_upstream.py` is a deterministic dry-run upstream (both wire
formats, SSE, optional key check, optional header capture, and a deterministic
canary leak when the user asks it to "repeat your system prompt"), so the
whole flow can be rehearsed without a real model.

`--events` is the host path of the gateway's events file (for the container
deployment: `podman exec sec-api-gateway cat /var/log/llmgw/events.jsonl` or a
bind mount). `--canary` only works if the gateway has that canary configured
via `canary_tokens_env`. `INDETERMINATE` results are real-model behavior (the
model refused to leak the canary), not failures.

