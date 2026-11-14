# syntax=docker/dockerfile:1

# --- Builder: install the project and its dependencies with uv ---------------
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS builder

WORKDIR /app

# Dependencies first (uv.lock pins exact versions; --frozen fails on drift).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the project itself, installed (as a wheel, not editable) into the same .venv.
# --no-editable is required: an editable install writes a .pth pointing at
# /app/src, which does not exist in the runtime image.
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# --- Runtime: slim image, non-root, healthcheck on /healthz ------------------
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

RUN groupadd --system llmgw \
    && useradd --system --gid llmgw --home-dir /app llmgw \
    && mkdir -p /var/log/llmgw \
    && chown llmgw:llmgw /var/log/llmgw

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    LLMGW_CONFIG=/config/gateway.yaml

USER llmgw
EXPOSE 8080

# No curl on slim; probe /healthz with the bundled Python.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import sys, urllib.request, yaml; c = yaml.safe_load(open('/config/gateway.yaml', encoding='utf-8')) or {}; p = int((c.get('listen') or {}).get('port', 8080)); r = urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz', timeout=2); sys.exit(0 if r.status == 200 else 1)"

# The `llmgw` entry point reads /config/gateway.yaml (LLMGW_CONFIG) and
# listens on the config's listen.host / listen.port.
CMD ["llmgw"]
