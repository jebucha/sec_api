"""App routes, model listing, and startup rule loading.

Covers T-LLM-PROXY-003, -009; T-DETECTION-RULES-017, -018.
"""

from __future__ import annotations

import httpx
import pytest
from starlette.testclient import TestClient

from helpers import (
    CHAT_BODY,
    auth_headers,
    ensure_rules_dir,
    events_of_type,
    make_test_config,
    make_upstream_handler,
    read_events,
    rule_dict,
    write_rule_file,
)
from llmgw.app import create_app


def _app(tmp_path, *, config=None, records=None):
    config = config or make_test_config(tmp_path)
    ensure_rules_dir(config)
    records = records if records is not None else []
    transport = httpx.MockTransport(make_upstream_handler(records))
    return create_app(config, upstream_transport=transport), config, records


def test_unsupported_path_returns_404_without_upstream_contact(tmp_path):
    """T-LLM-PROXY-003: an unserved path 404s and never touches an upstream."""
    app, _config, records = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/v1/embeddings", headers=auth_headers())
    assert response.status_code == 404
    assert records == []


def test_models_lists_exactly_routing_map(tmp_path):
    """T-LLM-PROXY-009: GET /v1/models returns exactly the routing map's models."""
    app, _config, _records = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/v1/models", headers=auth_headers())
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    ids = {entry["id"] for entry in payload["data"]}
    assert ids == {"qwen3-32b", "llama-3.1-8b"}
    assert len(payload["data"]) == 2


def test_startup_with_one_invalid_rule_serves_valid_rules(tmp_path):
    """T-DETECTION-RULES-017: one bad rule is rejected; the rest serve."""
    config = make_test_config(tmp_path, mode="enforce")
    rules_dir = ensure_rules_dir(config)
    write_rule_file(
        rules_dir,
        "pack.jsonl",
        [
            rule_dict(),  # valid: keyword "trigger phrase", block, request
            {"id": "BAD", "name": "broken", "version": "1", "stage": "request",
             "target": ["user_text"], "type": "keyword", "severity": "high",
             "keywords": ["x"]},  # missing action -> rejected
        ],
    )
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        blocked = client.post(
            "/v1/chat/completions",
            json={**CHAT_BODY, "messages": [{"role": "user", "content": "has a trigger phrase"}]},
            headers=auth_headers(),
        )
        assert blocked.status_code == 403
        clean = client.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers())
        assert clean.status_code == 200
        assert len(records) == 1

    events = read_events(config.events.path)
    rejected = events_of_type(events, "rule_rejected")
    assert len(rejected) == 1
    assert rejected[0]["id"] == "BAD"
    assert "action" in rejected[0]["reason"]


def test_startup_refuses_unreadable_rules_dir(tmp_path):
    """T-DETECTION-RULES-018: an unreadable rules directory fails startup, naming it."""
    config = make_test_config(tmp_path, rules_dir=str(tmp_path / "no-such-dir"))
    app = create_app(config)
    with pytest.raises(RuntimeError, match="no-such-dir"):
        with TestClient(app):
            pass
