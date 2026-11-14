"""Model routing, header forwarding policy, and credential boundaries.

Covers T-LLM-PROXY-006..008, -011..013.
"""

from __future__ import annotations

import httpx
from starlette.testclient import TestClient

from helpers import (
    CHAT_BODY,
    TEST_TOKEN,
    TEST_UPSTREAM_KEY,
    auth_headers,
    ensure_rules_dir,
    make_test_config,
    make_upstream_handler,
)
from llmgw.app import create_app
from llmgw.config import Upstream, UpstreamAuth
from llmgw.routing import forward_headers, inject_upstream_credential, upstream_url


def _app(tmp_path, records, *, config=None):
    config = config or make_test_config(tmp_path)
    ensure_rules_dir(config)
    transport = httpx.MockTransport(make_upstream_handler(records))
    return create_app(config, upstream_transport=transport)


def test_mapped_model_routes_to_upstream_same_path(tmp_path):
    """T-LLM-PROXY-006: model qwen3-32b -> gpu-box base URL, same API path."""
    records: list[httpx.Request] = []
    app = _app(tmp_path, records)
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers())
    assert response.status_code == 200
    assert len(records) == 1
    assert str(records[0].url) == "http://gpu-box.local:8000/v1/chat/completions"
    assert records[0].method == "POST"


def test_unknown_model_returns_404_without_upstream_contact(tmp_path):
    """T-LLM-PROXY-007: unmapped model -> provider-shaped 404, no upstream."""
    records: list[httpx.Request] = []
    app = _app(tmp_path, records)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={**CHAT_BODY, "model": "does-not-exist"},
            headers=auth_headers(),
        )
    assert response.status_code == 404
    assert records == []
    error = response.json()["error"]
    assert error["code"] == "model_not_found"
    assert "does-not-exist" in error["message"]
    assert response.headers["x-gateway-request-id"]


def test_missing_model_or_invalid_json_returns_400(tmp_path):
    """T-LLM-PROXY-008: no model field, or invalid JSON -> 400, no upstream."""
    records: list[httpx.Request] = []
    app = _app(tmp_path, records)
    with TestClient(app) as client:
        no_model = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers(),
        )
        assert no_model.status_code == 400
        assert no_model.json()["error"]["type"] == "invalid_request_error"

        invalid_json = client.post(
            "/v1/chat/completions",
            content=b"not json at all",
            headers={**auth_headers(), "content-type": "application/json"},
        )
        assert invalid_json.status_code == 400
        assert invalid_json.json()["error"]["type"] == "invalid_request_error"

    assert records == []


def test_client_token_stripped_before_forwarding(tmp_path):
    """T-LLM-PROXY-011: the client's gateway token never reaches the upstream."""
    records: list[httpx.Request] = []
    app = _app(tmp_path, records)
    with TestClient(app) as client:
        bearer = client.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers("bearer"))
        assert bearer.status_code == 200
        # Auth-less upstream: the injected credential is also absent, so the
        # client's x-api-key token must simply be gone.
        noauth = client.post(
            "/v1/chat/completions",
            json={"model": "llama-3.1-8b", "messages": CHAT_BODY["messages"]},
            headers=auth_headers("x-api-key"),
        )
        assert noauth.status_code == 200

    for request in records:
        for _name, value in request.headers.multi_items():
            assert TEST_TOKEN not in value
        assert request.headers.get("x-api-key") is None


def test_bearer_upstream_credential_injected(tmp_path):
    """T-LLM-PROXY-012: gpu-box (bearer) gets Authorization from its secret."""
    records: list[httpx.Request] = []
    app = _app(tmp_path, records)
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers())
    assert response.status_code == 200
    assert records[0].headers["authorization"] == f"Bearer {TEST_UPSTREAM_KEY}"
    assert TEST_TOKEN not in records[0].headers.get("authorization", "")


def test_no_auth_upstream_receives_no_credential(tmp_path):
    """T-LLM-PROXY-013: mac-mini (auth: none) gets no credential headers."""
    records: list[httpx.Request] = []
    app = _app(tmp_path, records)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "llama-3.1-8b", "messages": CHAT_BODY["messages"]},
            headers=auth_headers(),
        )
    assert response.status_code == 200
    assert records[0].headers.get("authorization") is None
    assert records[0].headers.get("x-api-key") is None


def test_x_api_key_upstream_credential_injected(tmp_path):
    """T-LLM-PROXY-012: an x-api-key upstream gets x-api-key from its secret."""
    x_key = "lmstudio-x-key-1"
    config = make_test_config(
        tmp_path,
        upstreams={
            "lmstudio": Upstream(
                name="lmstudio",
                url="http://lmstudio.local:1234",
                auth=UpstreamAuth(style="x-api-key", secret=x_key),
            ),
        },
        models={"llama-3.1-8b": "lmstudio"},
    )
    records: list[httpx.Request] = []
    app = _app(tmp_path, records, config=config)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "llama-3.1-8b", "messages": CHAT_BODY["messages"]},
            headers=auth_headers(),
        )
    assert response.status_code == 200
    assert records[0].headers["x-api-key"] == x_key
    # The client's own token must not reach the upstream, in any header.
    assert TEST_TOKEN not in records[0].headers.get("x-api-key", "")
    assert TEST_TOKEN not in records[0].headers.get("authorization", "")


def test_hop_by_hop_headers_not_forwarded(tmp_path):
    """D4a: hop-by-hop and framing headers are dropped; custom ones pass through."""
    records: list[httpx.Request] = []
    app = _app(tmp_path, records)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json=CHAT_BODY,
            headers={
                **auth_headers(),
                "connection": "close, X-Special",
                "x-special": "gone",
                "keep-alive": "timeout=5",
                "proxy-authorization": "Basic abc",
                "te": "trailers",
                "trailer": "x-foo",
                "upgrade": "h2c",
                "x-custom": "kept",
            },
        )
    assert response.status_code == 200
    upstream = records[0].headers
    # Client-supplied hop-by-hop / Connection-named headers are all dropped.
    for name in ("keep-alive", "proxy-authorization", "te", "trailer", "upgrade", "x-special"):
        assert upstream.get(name) is None
    assert upstream.get("connection") != "close, X-Special"
    # Host is the upstream's (httpx sets it from the URL), not the client's.
    assert upstream["host"] == "gpu-box.local:8000"
    assert upstream["x-custom"] == "kept"
    assert upstream["accept-encoding"] == "identity"


def test_forward_headers_unit():
    shaped = forward_headers(
        {
            "Authorization": "Bearer client-token",
            "x-api-key": "client-token",
            "Host": "gateway:8080",
            "Content-Length": "42",
            "Connection": "close, X-Bogus",
            "X-Bogus": "1",
            "X-Keep": "yes",
            "Accept-Encoding": "gzip",
        }
    )
    assert "authorization" not in shaped
    assert "x-api-key" not in shaped
    assert "host" not in shaped
    assert "content-length" not in shaped
    assert "connection" not in shaped
    assert "x-bogus" not in shaped
    assert shaped["x-keep"] == "yes"
    assert shaped["accept-encoding"] == "identity"


def test_credential_injection_unit(tmp_path):
    config = make_test_config(tmp_path)
    headers = forward_headers({"accept-encoding": "gzip"})
    inject_upstream_credential(headers, config.upstreams["gpu-box"])
    assert headers["authorization"] == f"Bearer {TEST_UPSTREAM_KEY}"
    headers2 = forward_headers({})
    inject_upstream_credential(headers2, config.upstreams["mac-mini"])
    assert "authorization" not in headers2
    assert "x-api-key" not in headers2
    headers3 = forward_headers({})
    inject_upstream_credential(
        headers3,
        Upstream(
            name="lmstudio",
            url="http://lmstudio.local:1234",
            auth=UpstreamAuth(style="x-api-key", secret="lmstudio-x-key-1"),
        ),
    )
    assert headers3["x-api-key"] == "lmstudio-x-key-1"
    assert "authorization" not in headers3
    assert upstream_url(config.upstreams["gpu-box"], "/v1/chat/completions") == (
        "http://gpu-box.local:8000/v1/chat/completions"
    )
