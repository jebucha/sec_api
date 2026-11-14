"""Client token authentication through the app.

Covers T-CLIENT-AUTH-001..006 and T-LLM-PROXY-026.
"""

from __future__ import annotations

import httpx
from starlette.testclient import TestClient

from helpers import (
    CHAT_BODY,
    TEST_TOKEN,
    TEST_TOKEN_SHA256,
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


def _app(tmp_path, *, config=None):
    config = config or make_test_config(tmp_path)
    ensure_rules_dir(config)
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    return app, config, records


def test_missing_token_returns_401_without_upstream_contact(tmp_path):
    """T-CLIENT-AUTH-001: no token -> 401, provider-shaped, no upstream contact."""
    app, _config, records = _app(tmp_path)
    with TestClient(app) as client:
        openai_resp = client.post("/v1/chat/completions", json=CHAT_BODY)
        assert openai_resp.status_code == 401
        assert openai_resp.json()["error"]["type"] == "invalid_request_error"

        anthropic_resp = client.post(
            "/v1/messages",
            json={"model": "llama-3.1-8b", "messages": CHAT_BODY["messages"]},
            headers={"x-api-key": ""},
        )
        assert anthropic_resp.status_code == 401
        assert anthropic_resp.json()["error"]["type"] == "authentication_error"

    assert records == []


def test_invalid_token_returns_401_and_event_omits_token(tmp_path):
    """T-CLIENT-AUTH-002: unknown token -> 401 + failure event without the token."""
    wrong = "totally-wrong-token"
    app, config, records = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions", json=CHAT_BODY, headers=auth_headers(token=wrong)
        )
    assert response.status_code == 401
    assert records == []
    events = read_events(config.events.path)
    auth_failed = events_of_type(events, "auth_failed")
    assert len(auth_failed) == 1
    completed = events_of_type(events, "request_completed")
    assert [e["outcome"] for e in completed] == ["auth_failed"]
    assert wrong not in str(events)


def test_bearer_header_authenticates(tmp_path):
    """T-CLIENT-AUTH-003: Authorization: Bearer <token> is accepted."""
    app, _config, records = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions", json=CHAT_BODY, headers=auth_headers("bearer")
        )
    assert response.status_code == 200
    assert len(records) == 1


def test_x_api_key_header_authenticates(tmp_path):
    """T-CLIENT-AUTH-004: x-api-key: <token> is accepted."""
    app, _config, records = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions", json=CHAT_BODY, headers=auth_headers("x-api-key")
        )
    assert response.status_code == 200
    assert len(records) == 1


def test_sha256_digest_in_config_authenticates_plaintext_token(tmp_path):
    """T-CLIENT-AUTH-005: config stores only the digest; the plaintext works."""
    app, config, _records = _app(tmp_path)
    digests = [client.token_sha256 for client in config.clients.values()]
    assert TEST_TOKEN_SHA256 in digests
    assert all(TEST_TOKEN not in digest for digest in digests)
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers())
    assert response.status_code == 200


def test_events_carry_client_name(tmp_path):
    """T-CLIENT-AUTH-006: every event for an authenticated request names the client."""
    config = make_test_config(tmp_path)
    rules_dir = ensure_rules_dir(config)
    write_rule_file(rules_dir, "flag.jsonl", [rule_dict(action="flag")])
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={**CHAT_BODY, "messages": [{"role": "user", "content": "a trigger phrase here"}]},
            headers=auth_headers(),
        )
    assert response.status_code == 200
    request_id = response.headers["x-gateway-request-id"]
    events = [
        event
        for event in read_events(config.events.path)
        if event.get("request_id") == request_id
    ]
    assert events, "no events for the request"
    assert {event["client"] for event in events} == {"opencode-laptop"}


def test_healthz_unauthenticated_returns_status_ok_only(tmp_path):
    """T-CLIENT-AUTH-007: GET /healthz needs no token and reveals nothing."""
    app, _config, records = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert set(response.json()) == {"status"}
    assert "x-gateway-request-id" not in response.headers
    assert records == []


def test_401_response_carries_request_id_header(tmp_path):
    """T-LLM-PROXY-026: the 401 carries X-Gateway-Request-Id matching its events."""
    app, config, _records = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions", json=CHAT_BODY, headers=auth_headers(token="nope")
        )
    assert response.status_code == 401
    request_id = response.headers["x-gateway-request-id"]
    events = read_events(config.events.path)
    auth_failed = events_of_type(events, "auth_failed")
    completed = events_of_type(events, "request_completed")
    assert len(auth_failed) == 1 and len(completed) == 1
    assert auth_failed[0]["request_id"] == request_id
    assert completed[0]["request_id"] == request_id
