"""Shared test helpers."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import yaml

from llmgw.config import Client, EventsConfig, GatewayConfig, Upstream, UpstreamAuth
from llmgw.rules.heuristics import Span
from llmgw.rules.ruleset import Rule, Ruleset, ruleset_hash


def make_rule(
    *,
    rule_id: str = "T-1",
    matches: str | None = "trigger phrase",
    raises: Exception | None = None,
    stage: str = "request",
    target: tuple[str, ...] = ("user_text",),
    action: str = "flag",
    mask_excerpt: bool = False,
    enabled: bool = True,
    severity: str = "high",
    owasp: tuple[str, ...] = (),
    atlas: tuple[str, ...] = (),
) -> Rule:
    """Build a Rule whose matcher finds ``matches`` as a substring (or raises)."""

    def matcher(segment: str) -> list[Span]:
        if raises is not None:
            raise raises
        spans: list[Span] = []
        if matches is None:
            return spans
        start = 0
        while True:
            index = segment.find(matches, start)
            if index == -1:
                break
            spans.append(Span(index, index + len(matches)))
            start = index + 1
        return spans

    return Rule(
        id=rule_id,
        name=rule_id,
        version="1",
        stage=stage,
        target=target,
        type="keyword",
        severity=severity,
        action=action,
        enabled=enabled,
        description=None,
        owasp=owasp,
        atlas=atlas,
        tags=(),
        mask_excerpt=mask_excerpt,
        file="test.jsonl",
        line=1,
        matcher=matcher,
        raw={"id": rule_id},
    )


def make_ruleset(*rules: Rule) -> Ruleset:
    return Ruleset(rules=tuple(rules), hash=ruleset_hash(list(rules)))

TEST_TOKEN = "client-token-1"
TEST_TOKEN_SHA256 = hashlib.sha256(TEST_TOKEN.encode()).hexdigest()
TEST_UPSTREAM_KEY = "vllm-upstream-key-1"


def make_test_config(tmp_path: Path, **overrides: Any) -> GatewayConfig:
    """A GatewayConfig built directly (no YAML) for unit tests."""
    base: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 8080,
        "mode": "monitor",
        "uninspected_policy": "flag",
        "debug_verdicts": False,
        "upstream_timeout_s": 5,
        "max_buffer_bytes": 1024 * 1024,
        "max_request_bytes": 8 * 1024 * 1024,
        "excerpt_max_chars": 200,
        "rules_dir": str(tmp_path / "rules"),
        "events": EventsConfig(path=str(tmp_path / "events.jsonl")),
        "canary_tokens_env": "LLMGW_CANARIES",
        "upstreams": {
            "gpu-box": Upstream(
                name="gpu-box",
                url="http://gpu-box.local:8000",
                auth=UpstreamAuth(style="bearer", secret=TEST_UPSTREAM_KEY),
            ),
            "mac-mini": Upstream(
                name="mac-mini",
                url="http://mac-mini.local:1234",
                auth=UpstreamAuth(style="none"),
            ),
        },
        "models": {"qwen3-32b": "gpu-box", "llama-3.1-8b": "mac-mini"},
        "clients": {
            "opencode-laptop": Client(name="opencode-laptop", token_sha256=TEST_TOKEN_SHA256)
        },
    }
    base.update(overrides)
    return GatewayConfig(**base)


def default_config_dict(tmp_path: Path) -> dict[str, Any]:
    """A valid in-test config as a dict (secrets resolved from env)."""
    return {
        "listen": {"host": "127.0.0.1", "port": 8080},
        "mode": "monitor",
        "uninspected_policy": "flag",
        "debug_verdicts": False,
        "upstream_timeout_s": 5,
        "max_buffer_bytes": 1024 * 1024,
        "max_request_bytes": 8 * 1024 * 1024,
        "excerpt_max_chars": 200,
        "rules_dir": str(tmp_path / "rules"),
        "events": {"path": str(tmp_path / "events.jsonl"), "stdout": False},
        "canary_tokens_env": "LLMGW_CANARIES",
        "upstreams": {
            "gpu-box": {
                "url": "http://10.0.0.20:8000",
                "auth": {"style": "bearer", "secret_env": "VLLM_KEY"},
            },
            "mac-mini": {"url": "http://10.0.0.21:1234", "auth": {"style": "none"}},
        },
        "models": {"qwen3-32b": "gpu-box", "llama-3.1-8b": "mac-mini"},
        "clients": {"opencode-laptop": {"token_sha256": TEST_TOKEN_SHA256}},
    }


def write_config(tmp_path: Path, **overrides: Any) -> Path:
    """Write a gateway.yaml with the given top-level overrides applied."""
    data = default_config_dict(tmp_path)
    for key, value in overrides.items():
        if key in data and isinstance(data[key], dict) and isinstance(value, dict):
            merged = copy.deepcopy(data[key])
            merged.update(value)
            data[key] = merged
        else:
            data[key] = value
    path = tmp_path / "gateway.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def scan_events_for_secrets(events: list[dict[str, Any]], secrets: list[str]) -> list[str]:
    """Return the secret values that appear anywhere in the serialized events."""
    blob = json.dumps(events, default=str, ensure_ascii=False)
    return [secret for secret in secrets if secret in blob]


def assert_no_secrets_in_events(events: list[dict[str, Any]], secrets: list[str]) -> None:
    """Redaction guard: fail if any known secret value reaches the event stream.

    ``secrets`` are values only the test knows (client tokens, upstream keys);
    they must never be written to events.
    """
    leaked = scan_events_for_secrets(events, secrets)
    assert not leaked, f"secrets leaked into events: {leaked!r}"


# --- in-process gateway helpers ------------------------------------------------

# Default successful upstream responses (valid in each wire format).
OAI_OK_BODY = (
    b'{"id":"chatcmpl-1","object":"chat.completion","created":0,"model":"qwen3-32b",'
    b'"choices":[{"index":0,"message":{"role":"assistant","content":"hello"},'
    b'"finish_reason":"stop"}],'
    b'"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}'
)
ANT_OK_BODY = (
    b'{"id":"msg_1","type":"message","role":"assistant","model":"llama-3.1-8b",'
    b'"content":[{"type":"text","text":"hello"}],"stop_reason":"end_stop",'
    b'"stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":1}}'
)

CHAT_BODY = {"model": "qwen3-32b", "messages": [{"role": "user", "content": "hello"}]}
ANT_CHAT_BODY = {"model": "llama-3.1-8b", "messages": [{"role": "user", "content": "hello"}]}


def auth_headers(style: str = "bearer", token: str = TEST_TOKEN) -> dict[str, str]:
    if style == "bearer":
        return {"authorization": f"Bearer {token}"}
    return {"x-api-key": token}


def read_events(path: str | Path) -> list[dict[str, Any]]:
    """All events in a JSONL events file, in file order."""
    p = Path(path)
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def events_of_type(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [event for event in events if event.get("event_type") == event_type]


def make_upstream_handler(
    records: list[httpx.Request],
    *,
    status: int = 200,
    body: bytes = OAI_OK_BODY,
    headers: dict[str, str] | None = None,
):
    """A MockTransport handler that records every upstream request."""

    def handler(request: httpx.Request) -> httpx.Response:
        records.append(request)
        return httpx.Response(status, content=body, headers=headers or {})

    return handler


def rule_dict(**overrides: Any) -> dict[str, Any]:
    """A valid keyword rule line; override fields per test."""
    rule: dict[str, Any] = {
        "id": "T-RULE",
        "name": "test rule",
        "version": "1",
        "stage": "request",
        "target": ["user_text"],
        "type": "keyword",
        "severity": "high",
        "action": "block",
        "enabled": True,
        "keywords": ["trigger phrase"],
    }
    rule.update(overrides)
    return rule


def write_rule_file(rules_dir: Path, filename: str, rules: list[Any]) -> Path:
    """Write a .jsonl pack; each entry is a dict (serialized) or a raw line."""
    rules_dir.mkdir(parents=True, exist_ok=True)
    path = rules_dir / filename
    lines = [
        json.dumps(rule, ensure_ascii=False) if isinstance(rule, dict) else rule for rule in rules
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def ensure_rules_dir(config: GatewayConfig) -> Path:
    """The configured rules directory, created if missing (empty = no rules)."""
    rules_dir = Path(config.rules_dir)
    rules_dir.mkdir(parents=True, exist_ok=True)
    return rules_dir
