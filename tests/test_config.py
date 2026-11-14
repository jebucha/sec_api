"""Tests for config loading (design D10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import TEST_TOKEN_SHA256, TEST_UPSTREAM_KEY, write_config
from llmgw.config import ConfigError, GatewayConfig, load_config, resolve_canary_tokens


@pytest.fixture
def environ() -> dict[str, str]:
    return {"VLLM_KEY": TEST_UPSTREAM_KEY, "LLMGW_CANARIES": "canary-a,canary-b"}


def test_valid_config_loads(tmp_path, environ):
    config = load_config(write_config(tmp_path), environ=environ)
    assert isinstance(config, GatewayConfig)
    assert config.mode == "monitor"
    assert config.uninspected_policy == "flag"
    assert config.max_request_bytes == 8 * 1024 * 1024
    assert config.clients["opencode-laptop"].token_sha256 == TEST_TOKEN_SHA256
    assert config.upstreams["gpu-box"].auth.style == "bearer"
    assert config.upstreams["gpu-box"].auth.secret == TEST_UPSTREAM_KEY
    assert config.upstreams["mac-mini"].auth.secret is None
    assert config.models == {"qwen3-32b": "gpu-box", "llama-3.1-8b": "mac-mini"}
    assert resolve_canary_tokens(config, environ=environ) == ["canary-a", "canary-b"]


def test_missing_secret_fails_startup(tmp_path):
    path = write_config(tmp_path)
    with pytest.raises(ConfigError, match="VLLM_KEY"):
        load_config(path, environ={})


def test_secret_from_file(tmp_path):
    secret_file = tmp_path / "secrets" / "vllm-key"
    secret_file.parent.mkdir()
    secret_file.write_text("file-backed-key\n", encoding="utf-8")
    overrides = {
        "upstreams": {
            "gpu-box": {
                "url": "http://10.0.0.20:8000",
                "auth": {"style": "bearer", "secret_file": str(secret_file)},
            },
            "mac-mini": {"url": "http://10.0.0.21:1234", "auth": {"style": "none"}},
        }
    }
    config = load_config(write_config(tmp_path, **overrides), environ={})
    assert config.upstreams["gpu-box"].auth.secret == "file-backed-key"


def test_empty_secret_file_fails_startup(tmp_path):
    """An empty secret file counts as a missing secret (no empty credential)."""
    secret_file = tmp_path / "secrets" / "empty-key"
    secret_file.parent.mkdir()
    secret_file.write_text("  \n", encoding="utf-8")
    overrides = {
        "upstreams": {
            "gpu-box": {
                "url": "http://10.0.0.20:8000",
                "auth": {"style": "bearer", "secret_file": str(secret_file)},
            },
            "mac-mini": {"url": "http://10.0.0.21:1234", "auth": {"style": "none"}},
        }
    }
    with pytest.raises(ConfigError, match="not found"):
        load_config(write_config(tmp_path, **overrides), environ={})


def test_dangling_model_upstream_reference_fails_startup(tmp_path):
    path = write_config(tmp_path, models={"mystery-model": "ghost-box"})
    with pytest.raises(ConfigError, match="unknown upstream"):
        load_config(path, environ={"VLLM_KEY": "x"})


def test_invalid_mode_fails_startup(tmp_path):
    path = write_config(tmp_path, mode="aggressive")
    with pytest.raises(ConfigError, match="mode"):
        load_config(path, environ={"VLLM_KEY": "x"})


def test_invalid_client_hash_fails_startup(tmp_path):
    path = write_config(tmp_path, clients={"laptop": {"token_sha256": "not-a-hash"}})
    with pytest.raises(ConfigError, match="token_sha256"):
        load_config(path, environ={"VLLM_KEY": "x"})


def test_example_config_loads(tmp_path, environ):
    """config/gateway.example.yaml must parse as a valid config."""
    example = Path(__file__).parent.parent / "config" / "gateway.example.yaml"
    config = load_config(example, environ=environ)
    assert config.models
    assert config.clients
