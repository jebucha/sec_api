"""Non-streaming proxy: passthrough, size limit, blocks, failures.

Covers T-LLM-PROXY-001, -002, -004, -005, -010, -014, -020..-025;
T-DETECTION-ENGINE-001, -006, -007; T-SECURITY-EVENTS-004;
T-CONTENT-NORMALIZATION-012, -013; and the app-level duplicate-key 400.
"""

from __future__ import annotations

import httpx
from starlette.testclient import TestClient

from helpers import (
    ANT_OK_BODY,
    CHAT_BODY,
    TEST_TOKEN,
    TEST_UPSTREAM_KEY,
    assert_no_secrets_in_events,
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


def _app(tmp_path, *, config=None, records=None, body=None, handler=None):
    config = config or make_test_config(tmp_path)
    ensure_rules_dir(config)
    records = records if records is not None else []
    if handler is None:
        handler = make_upstream_handler(records, body=body if body is not None else ANT_OK_BODY)
    app = create_app(config, upstream_transport=httpx.MockTransport(handler))
    return app, config, records


def _block_rule(rules_dir, **overrides):
    return write_rule_file(rules_dir, "block.jsonl", [rule_dict(**overrides)])


def test_openai_chat_proxied_unchanged(tmp_path):
    """T-LLM-PROXY-001: upstream status and body returned unchanged."""
    app, _config, records = _app(tmp_path, body=ANT_OK_BODY)
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers())
    assert response.status_code == 200
    assert response.content == ANT_OK_BODY
    assert len(records) == 1


def test_anthropic_messages_proxied_unchanged(tmp_path):
    """T-LLM-PROXY-002: /v1/messages relays the upstream response unchanged."""
    body = b'{"status": "error", "err": "boom"}'
    app, _config, records = _app(tmp_path, body=body)
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages",
            json={"model": "llama-3.1-8b", "messages": CHAT_BODY["messages"]},
            headers=auth_headers(),
        )
    assert response.status_code == 200
    assert response.content == body
    assert len(records) == 1


def test_oversized_request_rejected_413(tmp_path):
    """T-LLM-PROXY-004: body over max_request_bytes -> 413, no upstream contact."""
    config = make_test_config(tmp_path, max_request_bytes=100)
    records: list[httpx.Request] = []
    app, _c, _r = _app(tmp_path, config=config, records=records)
    big = {**CHAT_BODY, "messages": [{"role": "user", "content": "x" * 500}]}
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=big, headers=auth_headers())
    assert response.status_code == 413
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert records == []


def test_allowed_response_carries_request_id_header(tmp_path):
    """T-LLM-PROXY-005: X-Gateway-Request-Id equals the completed event's request_id."""
    app, config, _records = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers())
    assert response.status_code == 200
    request_id = response.headers["x-gateway-request-id"]
    completed = events_of_type(read_events(config.events.path), "request_completed")
    assert len(completed) == 1
    assert completed[0]["request_id"] == request_id


def test_forwarded_body_byte_identical(tmp_path):
    """T-LLM-PROXY-010: the upstream receives the exact client body bytes."""
    # Escaped unicode + newline: the exact bytes must survive to the upstream.
    body = (
        b'{"model": "qwen3-32b", '
        b'"messages": [{"role": "user", "content": "h\\u00e9\\u2603\\n"}]}'
    )
    records: list[httpx.Request] = []
    app, _config, _r = _app(tmp_path, records=records)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            content=body,
            headers={**auth_headers(), "content-type": "application/json"},
        )
    assert response.status_code == 200
    assert records[0].content == body


def test_upstream_credential_absent_from_responses_and_events(tmp_path):
    """T-LLM-PROXY-014: no response or event ever carries a credential value."""
    config = make_test_config(tmp_path, mode="enforce")
    rules_dir = ensure_rules_dir(config)
    _block_rule(rules_dir)
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        allowed = client.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers())
        blocked = client.post(
            "/v1/chat/completions",
            json={**CHAT_BODY, "messages": [{"role": "user", "content": "the trigger phrase"}]},
            headers=auth_headers(),
        )
    assert allowed.status_code == 200
    assert blocked.status_code == 403
    for secret in (TEST_TOKEN, TEST_UPSTREAM_KEY):
        assert secret not in allowed.text
        assert secret not in blocked.text
    assert_no_secrets_in_events(
        read_events(config.events.path), [TEST_TOKEN, TEST_UPSTREAM_KEY]
    )


def test_enforce_request_block_never_contacts_upstream(tmp_path):
    """T-DETECTION-ENGINE-001: an enforce-mode request block stops before upstream."""
    config = make_test_config(tmp_path, mode="enforce")
    rules_dir = ensure_rules_dir(config)
    _block_rule(rules_dir)
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={**CHAT_BODY, "messages": [{"role": "user", "content": "the trigger phrase"}]},
            headers=auth_headers(),
        )
    assert response.status_code == 403
    assert records == []


def test_monitor_block_forwards_and_records_not_enforced(tmp_path):
    """T-DETECTION-ENGINE-006: monitor mode forwards and records would-block."""
    config = make_test_config(tmp_path, mode="monitor")
    rules_dir = ensure_rules_dir(config)
    _block_rule(rules_dir)
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={**CHAT_BODY, "messages": [{"role": "user", "content": "the trigger phrase"}]},
            headers=auth_headers(),
        )
    assert response.status_code == 200
    assert len(records) == 1
    verdicts = events_of_type(read_events(config.events.path), "verdict")
    assert len(verdicts) == 1
    assert verdicts[0]["action"] == "block"
    assert verdicts[0]["enforced"] is False


def test_enforce_block_returns_block_and_records_enforced(tmp_path):
    """T-DETECTION-ENGINE-007: enforce mode blocks and records enforced: true."""
    config = make_test_config(tmp_path, mode="enforce")
    rules_dir = ensure_rules_dir(config)
    _block_rule(rules_dir)
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={**CHAT_BODY, "messages": [{"role": "user", "content": "the trigger phrase"}]},
            headers=auth_headers(),
        )
    assert response.status_code == 403
    assert records == []
    verdicts = events_of_type(read_events(config.events.path), "verdict")
    assert verdicts[0]["action"] == "block"
    assert verdicts[0]["enforced"] is True


def test_openai_block_response_shape(tmp_path):
    """T-LLM-PROXY-020: 403 with OpenAI-shaped error of type gateway_blocked."""
    config = make_test_config(tmp_path, mode="enforce")
    rules_dir = ensure_rules_dir(config)
    _block_rule(rules_dir)
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={**CHAT_BODY, "messages": [{"role": "user", "content": "trigger phrase"}]},
            headers=auth_headers(),
        )
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["type"] == "gateway_blocked"
    assert response.headers["x-gateway-event-id"]


def test_anthropic_block_response_shape(tmp_path):
    """T-LLM-PROXY-021: 403 with Anthropic-shaped error of type gateway_blocked."""
    config = make_test_config(tmp_path, mode="enforce")
    rules_dir = ensure_rules_dir(config)
    _block_rule(rules_dir, target=["user_text"])
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        response = client.post(
            "/v1/messages",
            json={
                "model": "llama-3.1-8b",
                "messages": [{"role": "user", "content": "trigger phrase"}],
            },
            headers=auth_headers(),
        )
    assert response.status_code == 403
    payload = response.json()
    assert payload["type"] == "error"
    assert payload["error"]["type"] == "gateway_blocked"
    assert response.headers["x-gateway-event-id"]


def _blocked_openai(app, client):
    return client.post(
        "/v1/chat/completions",
        json={**CHAT_BODY, "messages": [{"role": "user", "content": "trigger phrase"}]},
        headers=auth_headers(),
    )


def test_block_message_opaque_by_default(tmp_path):
    """T-LLM-PROXY-022: without debug_verdicts no rule id leaks into the message."""
    config = make_test_config(tmp_path, mode="enforce")
    rules_dir = ensure_rules_dir(config)
    _block_rule(rules_dir)
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        response = _blocked_openai(app, client)
    message = response.json()["error"]["message"]
    assert "T-RULE" not in message
    assert response.headers["x-gateway-event-id"] in message


def test_block_message_includes_rule_ids_with_debug_verdicts(tmp_path):
    """T-LLM-PROXY-023: debug_verdicts adds the matched rule ids."""
    config = make_test_config(tmp_path, mode="enforce", debug_verdicts=True)
    rules_dir = ensure_rules_dir(config)
    _block_rule(rules_dir)
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        response = _blocked_openai(app, client)
    message = response.json()["error"]["message"]
    assert "T-RULE" in message


def test_block_event_id_header_matches_verdict_event(tmp_path):
    """T-SECURITY-EVENTS-004: X-Gateway-Event-Id is the verdict event's event_id."""
    config = make_test_config(tmp_path, mode="enforce")
    rules_dir = ensure_rules_dir(config)
    _block_rule(rules_dir)
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        response = _blocked_openai(app, client)
    event_id = response.headers["x-gateway-event-id"]
    verdicts = events_of_type(read_events(config.events.path), "verdict")
    assert len(verdicts) == 1
    assert verdicts[0]["event_id"] == event_id


def _image_body() -> dict:
    return {
        "model": "qwen3-32b",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look at this"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            }
        ],
    }


def test_fail_closed_blocks_image_without_upstream_contact(tmp_path):
    """T-CONTENT-NORMALIZATION-012: fail_closed blocks an uninspected image part."""
    config = make_test_config(tmp_path, uninspected_policy="fail_closed")
    ensure_rules_dir(config)
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=_image_body(), headers=auth_headers())
    assert response.status_code == 403
    assert records == []
    events = read_events(config.events.path)
    gaps = events_of_type(events, "uninspected_content")
    assert [gap["kind"] for gap in gaps] == ["image"]
    assert gaps[0]["stage"] == "request"
    verdicts = events_of_type(events, "verdict")
    assert verdicts[0]["action"] == "block"
    assert verdicts[0]["enforced"] is True


def test_fail_closed_forwards_inspected_tool_content(tmp_path):
    """T-CONTENT-NORMALIZATION-015: tool calls/results are inspected, not gapped."""
    config = make_test_config(tmp_path, uninspected_policy="fail_closed")
    ensure_rules_dir(config)
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    body = {
        "model": "qwen3-32b",
        "messages": [
            {"role": "user", "content": "list the files"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "shell", "arguments": '{"command": "ls"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "file.txt"},
        ],
    }
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=body, headers=auth_headers())
    assert response.status_code == 200  # forwarded: tool content is inspected
    assert len(records) == 1
    events = read_events(config.events.path)
    gaps = events_of_type(events, "uninspected_content")
    assert not gaps  # no tool_call/tool_result gaps, no other uninspected content


def test_flag_policy_forwards_image_and_records_event(tmp_path):
    """T-CONTENT-NORMALIZATION-013: default flag policy forwards and records the gap."""
    config = make_test_config(tmp_path)  # uninspected_policy defaults to "flag"
    ensure_rules_dir(config)
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=_image_body(), headers=auth_headers())
    assert response.status_code == 200
    assert len(records) == 1
    gaps = events_of_type(read_events(config.events.path), "uninspected_content")
    assert [gap["kind"] for gap in gaps] == ["image"]


def test_duplicate_gaps_collapse_into_one_event_with_count(tmp_path):
    """T-CONTENT-NORMALIZATION-031: 20 identical stream gaps -> one event, count 20."""
    config = make_test_config(tmp_path)  # monitor mode streams through the tee
    ensure_rules_dir(config)
    chunk = b'data: {"choices":[{"delta":{"mystery":"x"}}]}\n\n'
    body = chunk * 20 + b"data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )

    app = create_app(config, upstream_transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={**CHAT_BODY, "stream": True},
            headers=auth_headers(),
        )
    assert response.status_code == 200
    gaps = events_of_type(read_events(config.events.path), "uninspected_content")
    assert len(gaps) == 1
    assert gaps[0]["kind"] == "unknown_part"
    assert gaps[0]["location"] == "choices[0].delta"
    assert gaps[0]["stage"] == "response"
    assert gaps[0]["count"] == 20


def test_distinct_gaps_emit_separate_events_with_count_one(tmp_path):
    """T-CONTENT-NORMALIZATION-032: gaps with different locations stay separate."""
    config = make_test_config(tmp_path)
    ensure_rules_dir(config)
    records: list[httpx.Request] = []
    app = create_app(
        config, upstream_transport=httpx.MockTransport(make_upstream_handler(records))
    )
    body = {
        "model": "qwen3-32b",
        "tools": [{"type": "function"}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look at this"},
                    {"type": "image_url", "image_url": {}},
                ],
            }
        ],
    }
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=body, headers=auth_headers())
    assert response.status_code == 200
    gaps = events_of_type(read_events(config.events.path), "uninspected_content")
    assert [(gap["kind"], gap["location"], gap["stage"], gap["count"]) for gap in gaps] == [
        ("image", "messages[0].content[1]", "request", 1),
        ("tool_definitions", "tools", "request", 1),
    ]


def test_tool_results_match_in_verdict_event_with_bounded_excerpt(tmp_path):
    """6.2: a tool_results match reaches the verdict event with its field name
    and an excerpt bounded to ``excerpt_max_chars``."""
    config = make_test_config(tmp_path)  # monitor: forwards, records the verdict
    rules_dir = ensure_rules_dir(config)
    write_rule_file(
        rules_dir,
        "tools.jsonl",
        [
            {
                "id": "TR-RULE",
                "name": "long run in tool output",
                "version": "1",
                "stage": "request",
                "target": ["tool_results"],
                "type": "regex",
                "severity": "high",
                "action": "block",
                "enabled": True,
                "pattern": "y{500}",
            }
        ],
    )
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    tool_result = "prefix " + "y" * 500 + " tail"
    body = {
        "model": "qwen3-32b",
        "messages": [
            {"role": "user", "content": "summarize the output"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "shell"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": tool_result},
        ],
    }
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=body, headers=auth_headers())
    assert response.status_code == 200
    assert len(records) == 1
    verdicts = events_of_type(read_events(config.events.path), "verdict")
    assert len(verdicts) == 1
    assert verdicts[0]["stage"] == "request"
    (match,) = [m for m in verdicts[0]["matches"] if m["rule_id"] == "TR-RULE"]
    assert match["field"] == "tool_results"
    # the 500-char match is bounded to excerpt_max_chars (200 in the test config)
    assert match["excerpt"] == "y" * 200
    assert len(match["excerpt"]) == 200


def test_duplicate_key_body_returns_400(tmp_path):
    """App-level: a duplicate JSON key anywhere in the body is a 400, no upstream."""
    records: list[httpx.Request] = []
    app, _config, _r = _app(tmp_path, records=records)
    body = b'{"model": "qwen3-32b", "messages": [{"role": "user", "content": "a", "content": "b"}]}'
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            content=body,
            headers={**auth_headers(), "content-type": "application/json"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert records == []


def test_upstream_connection_refused_returns_502(tmp_path):
    """T-LLM-PROXY-024: an unreachable upstream yields a provider-shaped 502."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    app, config, records = _app(tmp_path, handler=handler)
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers())
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "server_error"
    assert records == []
    completed = events_of_type(read_events(config.events.path), "request_completed")
    assert completed[0]["outcome"] == "upstream_error"
    assert completed[0]["status"] == 502


def test_upstream_timeout_returns_504(tmp_path):
    """T-LLM-PROXY-025: a slow upstream yields a provider-shaped 504."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    app, config, _records = _app(tmp_path, handler=handler)
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers())
    assert response.status_code == 504
    assert response.json()["error"]["type"] == "server_error"
    completed = events_of_type(read_events(config.events.path), "request_completed")
    assert completed[0]["outcome"] == "upstream_error"
    assert completed[0]["status"] == 504


def test_unparseable_upstream_body_flagged(tmp_path):
    """Task 14.8: an unparseable response body is uninspected content."""
    config = make_test_config(tmp_path)  # uninspected_policy defaults to "flag"
    records: list[httpx.Request] = []
    app, _c, _r = _app(
        tmp_path, config=config, records=records, body=b"not json at all"
    )
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers())
    assert response.status_code == 200
    assert response.content == b"not json at all"
    assert len(records) == 1
    gaps = events_of_type(read_events(config.events.path), "uninspected_content")
    assert [(gap["kind"], gap["location"]) for gap in gaps] == [("other", "response.body")]


def test_unparseable_upstream_body_blocks_under_fail_closed(tmp_path):
    """Task 14.8: fail_closed blocks an unparseable upstream response body."""
    config = make_test_config(tmp_path, uninspected_policy="fail_closed")
    records: list[httpx.Request] = []
    app, _c, _r = _app(
        tmp_path, config=config, records=records, body=b"not json at all"
    )
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers())
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "gateway_blocked"
    assert b"not json" not in response.content
    assert len(records) == 1
    events = read_events(config.events.path)
    gaps = events_of_type(events, "uninspected_content")
    assert [(gap["kind"], gap["location"]) for gap in gaps] == [("other", "response.body")]
    verdicts = events_of_type(events, "verdict")
    assert verdicts[0]["stage"] == "response"
    assert verdicts[0]["action"] == "block"
    assert verdicts[0]["enforced"] is True


def test_enforce_response_stage_block_hides_upstream_content(tmp_path):
    """Extra: a response-stage block returns 403 with none of the upstream content."""
    config = make_test_config(tmp_path, mode="enforce")
    rules_dir = ensure_rules_dir(config)
    write_rule_file(
        rules_dir,
        "response.jsonl",
        [rule_dict(id="RESP-1", stage="response", target=["assistant_text"], keywords=["leakme"])],
    )
    body = b'{"choices":[{"index":0,"message":{"content":"leakme"}}]}'
    records: list[httpx.Request] = []
    app = create_app(
        config, upstream_transport=httpx.MockTransport(make_upstream_handler(records, body=body))
    )
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=CHAT_BODY, headers=auth_headers())
    assert response.status_code == 403
    assert len(records) == 1
    assert b"leakme" not in response.content
    verdicts = events_of_type(read_events(config.events.path), "verdict")
    assert verdicts[0]["stage"] == "response"
    assert verdicts[0]["enforced"] is True
