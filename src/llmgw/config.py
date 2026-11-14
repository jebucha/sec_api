"""Gateway configuration: YAML -> typed dataclasses (design D10).

Loaded at startup only. Missing secrets and unknown upstream references in
``models`` fail startup rather than failing the first request.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")

MODES = ("monitor", "enforce")
UNINSPECTED_POLICIES = ("flag", "fail_closed")
AUTH_STYLES = ("bearer", "x-api-key", "none")


class ConfigError(Exception):
    """Configuration is invalid; the gateway must not start."""


@dataclass(frozen=True)
class UpstreamAuth:
    style: str  # bearer | x-api-key | none
    secret: str | None = None  # resolved credential, or None for "none"


@dataclass(frozen=True)
class Upstream:
    name: str
    url: str
    auth: UpstreamAuth


@dataclass(frozen=True)
class Client:
    name: str
    token_sha256: str  # lowercase hex digest


@dataclass(frozen=True)
class EventsConfig:
    path: str | None
    stdout: bool = False


@dataclass(frozen=True)
class GatewayConfig:
    host: str
    port: int
    mode: str
    uninspected_policy: str
    debug_verdicts: bool
    upstream_timeout_s: float
    max_buffer_bytes: int
    max_request_bytes: int
    excerpt_max_chars: int
    rules_dir: str
    events: EventsConfig
    canary_tokens_env: str | None
    upstreams: dict[str, Upstream]
    models: dict[str, str]  # model name -> upstream name
    clients: dict[str, Client]

    def with_overrides(self, **kwargs: Any) -> GatewayConfig:
        return replace(self, **kwargs)


def resolve_secret(
    *,
    name: str,
    secret_env: str | None = None,
    secret_file: str | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    """Resolve a secret from an env var or a secret file.

    Raises ConfigError when neither is set or the referenced value is missing.
    """
    env = os.environ if environ is None else environ
    if secret_env:
        value = env.get(secret_env)
        if value:
            return value
    if secret_file:
        path = Path(secret_file)
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
    source = secret_env or secret_file or "no source configured"
    raise ConfigError(f"secret '{name}' not found: {source} is missing or empty")


def load_config(path: str | Path, environ: dict[str, str] | None = None) -> GatewayConfig:
    env = os.environ if environ is None else environ
    data: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping: {path}")

    listen = data.get("listen") or {}
    upstreams: dict[str, Upstream] = {}
    for name, spec in (data.get("upstreams") or {}).items():
        spec = spec or {}
        auth_spec = spec.get("auth") or {}
        style = auth_spec.get("style", "none")
        if style not in AUTH_STYLES:
            raise ConfigError(f"upstream '{name}': unknown auth style '{style}'")
        if style == "none":
            auth = UpstreamAuth(style="none")
        else:
            secret = resolve_secret(
                name=f"{name}.auth",
                secret_env=auth_spec.get("secret_env"),
                secret_file=auth_spec.get("secret_file"),
                environ=env,
            )
            auth = UpstreamAuth(style=style, secret=secret)
        upstreams[name] = Upstream(
            name=name,
            url=str(spec.get("url") or "").rstrip("/"),
            auth=auth,
        )

    models: dict[str, str] = {}
    for model, upstream_name in (data.get("models") or {}).items():
        if upstream_name not in upstreams:
            raise ConfigError(f"model '{model}' references unknown upstream '{upstream_name}'")
        models[model] = upstream_name

    clients: dict[str, Client] = {}
    for name, spec in (data.get("clients") or {}).items():
        spec = spec or {}
        digest = str(spec.get("token_sha256") or "")
        if not _SHA256_HEX.match(digest):
            raise ConfigError(f"client '{name}': token_sha256 must be a 64-char hex digest")
        clients[name] = Client(name=name, token_sha256=digest.lower())

    events_spec = data.get("events") or {}
    events = EventsConfig(
        path=events_spec.get("path"),
        stdout=bool(events_spec.get("stdout", False)),
    )

    mode = data.get("mode", "monitor")
    if mode not in MODES:
        raise ConfigError(f"mode must be one of {MODES}, got '{mode}'")
    policy = data.get("uninspected_policy", "flag")
    if policy not in UNINSPECTED_POLICIES:
        raise ConfigError(f"uninspected_policy must be one of {UNINSPECTED_POLICIES}")

    return GatewayConfig(
        host=str(listen.get("host", "0.0.0.0")),
        port=int(listen.get("port", 8080)),
        mode=mode,
        uninspected_policy=policy,
        debug_verdicts=bool(data.get("debug_verdicts", False)),
        upstream_timeout_s=float(data.get("upstream_timeout_s", 120)),
        max_buffer_bytes=int(data.get("max_buffer_bytes", 4 * 1024 * 1024)),
        max_request_bytes=int(data.get("max_request_bytes", 8 * 1024 * 1024)),
        excerpt_max_chars=int(data.get("excerpt_max_chars", 200)),
        rules_dir=str(data.get("rules_dir", "rules")),
        events=events,
        canary_tokens_env=data.get("canary_tokens_env"),
        upstreams=upstreams,
        models=models,
        clients=clients,
    )


def resolve_canary_tokens(
    config: GatewayConfig, environ: dict[str, str] | None = None
) -> list[str]:
    """Canary values from the env var named by ``canary_tokens_env`` (design D11)."""
    env = os.environ if environ is None else environ
    if not config.canary_tokens_env:
        return []
    raw = env.get(config.canary_tokens_env, "")
    return [token.strip() for token in raw.split(",") if token.strip()]
