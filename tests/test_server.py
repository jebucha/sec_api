"""Tests for the llmgw console entry point (task 14.5)."""

from __future__ import annotations

from helpers import TEST_UPSTREAM_KEY, write_config
from llmgw import server


def test_missing_config_exits_2_with_clean_error(tmp_path, capsys):
    assert server.main([str(tmp_path / "missing.yaml")]) == 2
    err = capsys.readouterr().err
    assert "config error" in err
    assert "missing.yaml" in err


def test_main_binds_configured_listen_address(tmp_path, monkeypatch):
    path = write_config(tmp_path, listen={"host": "127.0.0.1", "port": 18080})
    monkeypatch.setenv("VLLM_KEY", TEST_UPSTREAM_KEY)
    monkeypatch.setattr(server, "create_app", lambda config: object())
    calls: dict = {}

    def fake_run(app, **kwargs):
        calls.update(kwargs)

    monkeypatch.setattr(server.uvicorn, "run", fake_run)
    assert server.main([str(path)]) == 0
    assert calls == {"host": "127.0.0.1", "port": 18080}
