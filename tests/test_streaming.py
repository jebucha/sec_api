"""Streaming: monitor-mode tee and enforce-mode buffer+replay (design D9).

Covers T-LLM-PROXY-015..019.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
from starlette.testclient import TestClient

from helpers import (
    CHAT_BODY,
    auth_headers,
    ensure_rules_dir,
    events_of_type,
    make_test_config,
    read_events,
    write_rule_file,
)
from llmgw.app import create_app


class DelayedStream(httpx.AsyncByteStream):
    """A fake upstream stream that sleeps between chunks."""

    def __init__(self, chunks: list[bytes], delay: float) -> None:
        self._chunks = chunks
        self._delay = delay

    async def aclose(self) -> None:
        pass

    async def __aiter__(self):
        for index, chunk in enumerate(self._chunks):
            if index:
                await asyncio.sleep(self._delay)
            yield chunk


class ErrorStream(httpx.AsyncByteStream):
    """A fake upstream stream that yields its chunks, then raises."""

    def __init__(self, chunks: list[bytes], error: Exception) -> None:
        self._chunks = chunks
        self._error = error

    async def aclose(self) -> None:
        pass

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk
        raise self._error


def sse_chunks(parts: list[str]) -> list[bytes]:
    """OpenAI SSE data chunks for the given content deltas."""
    return [
        ("data: " + json.dumps({"choices": [{"delta": {"content": part}}]}) + "\n\n").encode()
        for part in parts
    ]


def _stream_app(tmp_path, chunks, *, config=None, delay=0.0, status=200, stream=None):
    config = config or make_test_config(tmp_path)
    ensure_rules_dir(config)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            headers={"content-type": "text/event-stream"},
            stream=stream if stream is not None else DelayedStream(chunks, delay),
        )

    app = create_app(config, upstream_transport=httpx.MockTransport(handler))
    return app, config


def _stream_body() -> str:
    return json.dumps({**CHAT_BODY, "stream": True})


def test_monitor_stream_relays_first_chunk_before_upstream_finishes(tmp_path):
    """T-LLM-PROXY-015: monitor mode relays chunks as they arrive; events after."""
    config = make_test_config(tmp_path, mode="monitor")
    rules_dir = ensure_rules_dir(config)
    write_rule_file(
        rules_dir,
        "response.jsonl",
        [
            {
                "id": "RESP-FLAG",
                "name": "r",
                "version": "1",
                "stage": "response",
                "target": ["assistant_text"],
                "type": "keyword",
                "severity": "low",
                "action": "flag",
                "enabled": True,
                "keywords": ["secretword"],
            }
        ],
    )
    # The upstream takes >= 1.0 s to produce the stream (two 0.5 s gaps).
    chunks = sse_chunks(["first ", "middle ", "secretword"])
    app, config = _stream_app(tmp_path, chunks, config=config, delay=0.5)
    with TestClient(app) as client:
        started = time.monotonic()
        first = None
        received = []
        with client.stream(
            "POST", "/v1/chat/completions", content=_stream_body(),
            headers={**auth_headers(), "content-type": "application/json"},
        ) as response:
            assert response.status_code == 200
            for chunk in response.iter_bytes():
                if first is None:
                    first = time.monotonic() - started
                received.append(chunk)
        total = time.monotonic() - started
    # A buffered (enforce-style) relay would only start after >= 1.0 s.
    assert first < 0.45, f"first chunk waited {first:.3f}s"
    assert total >= 0.9  # the full stream really did take that long
    assert b"".join(received) == b"".join(chunks)

    events = read_events(config.events.path)
    verdicts = events_of_type(events, "verdict")
    assert len(verdicts) == 1
    assert verdicts[0]["stage"] == "response"
    assert verdicts[0]["action"] == "flag"
    completed = events_of_type(events, "request_completed")
    assert completed[0]["outcome"] == "allowed"
    assert completed[0]["status"] == 200


def test_monitor_stream_overflow_relays_all_and_emits_event(tmp_path):
    """T-LLM-PROXY-016: past max_buffer_bytes the relay continues, gap recorded."""
    config = make_test_config(tmp_path, mode="monitor", max_buffer_bytes=64)
    chunks = sse_chunks(["a" * 40, "b" * 40, "c" * 40])
    app, config = _stream_app(tmp_path, chunks, config=config)
    with TestClient(app) as client:
        received = []
        with client.stream(
            "POST", "/v1/chat/completions", content=_stream_body(),
            headers={**auth_headers(), "content-type": "application/json"},
        ) as response:
            assert response.status_code == 200
            for chunk in response.iter_bytes():
                received.append(chunk)
    assert b"".join(received) == b"".join(chunks)  # complete stream relayed
    gaps = events_of_type(read_events(config.events.path), "uninspected_content")
    assert [gap["kind"] for gap in gaps] == ["stream_overflow"]
    assert gaps[0]["stage"] == "response"


def test_enforce_clean_stream_replayed_byte_identical(tmp_path):
    """T-LLM-PROXY-017: enforce mode replays the exact stream, after evaluation."""
    config = make_test_config(tmp_path, mode="enforce")
    chunks = sse_chunks(["one ", "two ", "three"])
    app, _config = _stream_app(tmp_path, chunks, config=config, delay=0.05)
    with TestClient(app) as client:
        received = []
        with client.stream(
            "POST", "/v1/chat/completions", content=_stream_body(),
            headers={**auth_headers(), "content-type": "application/json"},
        ) as response:
            assert response.status_code == 200
            for chunk in response.iter_bytes():
                received.append(chunk)
    assert b"".join(received) == b"".join(chunks)


def test_enforce_blocked_stream_returns_block_without_content(tmp_path):
    """T-LLM-PROXY-018: a blocking rule stops the stream with a 403, no content."""
    config = make_test_config(tmp_path, mode="enforce")
    rules_dir = ensure_rules_dir(config)
    write_rule_file(
        rules_dir,
        "response.jsonl",
        [
            {
                "id": "RESP-BLOCK",
                "name": "r",
                "version": "1",
                "stage": "response",
                "target": ["assistant_text"],
                "type": "keyword",
                "severity": "high",
                "action": "block",
                "enabled": True,
                "keywords": ["leakthis"],
            }
        ],
    )
    chunks = sse_chunks(["clean start", "leakthis is here"])
    app, _config = _stream_app(tmp_path, chunks, config=config)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions", content=_stream_body(),
            headers={**auth_headers(), "content-type": "application/json"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "gateway_blocked"
    assert b"leakthis" not in response.content
    assert b"clean start" not in response.content
    assert response.headers["x-gateway-event-id"]
    verdicts = events_of_type(read_events(_config.events.path), "verdict")
    assert verdicts[0]["stage"] == "response"
    assert verdicts[0]["enforced"] is True


def test_enforce_stream_overflow_blocked(tmp_path):
    """T-LLM-PROXY-019: enforce overflow is a detection error: block + rule_error."""
    config = make_test_config(tmp_path, mode="enforce", max_buffer_bytes=64)
    chunks = sse_chunks(["a" * 40, "b" * 40])
    app, _config = _stream_app(tmp_path, chunks, config=config)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions", content=_stream_body(),
            headers={**auth_headers(), "content-type": "application/json"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "gateway_blocked"
    assert b"aaa" not in response.content
    assert response.headers["x-gateway-event-id"]
    events = read_events(_config.events.path)
    errors = events_of_type(events, "rule_error")
    assert len(errors) == 1
    assert "stream" in errors[0]["rule_id"]
    assert "max_buffer_bytes" in errors[0]["error"]


# --- fail_closed buffers even in monitor mode (D12, validation fix) ------------


def test_monitor_fail_closed_stream_uninspected_is_blocked(tmp_path):
    """D12: fail_closed blocks uninspected streams even in monitor mode.

    A streamed response cannot be blocked after the fact, so under
    ``uninspected_policy: fail_closed`` the monitor stream is buffered and
    evaluated first, then blocked (403) if it carries uninspected content.
    """
    config = make_test_config(tmp_path, mode="monitor", uninspected_policy="fail_closed")
    chunks = sse_chunks(["clean start"]) + [
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0}]}}]}\n\n'
    ]
    app, _config = _stream_app(tmp_path, chunks, config=config)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions", content=_stream_body(),
            headers={**auth_headers(), "content-type": "application/json"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "gateway_blocked"
    assert b"clean start" not in response.content
    assert response.headers["x-gateway-event-id"]
    events = read_events(_config.events.path)
    gaps = events_of_type(events, "uninspected_content")
    assert [gap["kind"] for gap in gaps] == ["tool_call"]
    verdicts = events_of_type(events, "verdict")
    assert verdicts[0]["stage"] == "response"
    assert verdicts[0]["action"] == "block"
    assert verdicts[0]["enforced"] is True


def test_monitor_fail_closed_stream_clean_replays_byte_identical(tmp_path):
    """D12: a clean stream passes through the buffered path byte-identically."""
    config = make_test_config(tmp_path, mode="monitor", uninspected_policy="fail_closed")
    chunks = sse_chunks(["one ", "two ", "three"])
    app, _config = _stream_app(tmp_path, chunks, config=config, delay=0.05)
    with TestClient(app) as client:
        received = []
        with client.stream(
            "POST", "/v1/chat/completions", content=_stream_body(),
            headers={**auth_headers(), "content-type": "application/json"},
        ) as response:
            assert response.status_code == 200
            for chunk in response.iter_bytes():
                received.append(chunk)
    assert b"".join(received) == b"".join(chunks)
    completed = events_of_type(read_events(_config.events.path), "request_completed")
    assert completed[0]["outcome"] == "allowed"
    assert completed[0]["status"] == 200


# --- mid-stream upstream failures map to 504/502 (validation fix) --------------


def test_enforce_stream_upstream_timeout_maps_to_504(tmp_path):
    """A mid-stream upstream timeout is a 504, not a partial stream."""
    config = make_test_config(tmp_path, mode="enforce")
    chunks = sse_chunks(["start", "never arrives"])
    app, _config = _stream_app(
        tmp_path,
        chunks,
        config=config,
        stream=ErrorStream(chunks[:1], httpx.ReadTimeout("read timed out")),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions", content=_stream_body(),
            headers={**auth_headers(), "content-type": "application/json"},
        )
    assert response.status_code == 504
    assert b"start" not in response.content
    completed = events_of_type(read_events(_config.events.path), "request_completed")
    assert completed[0]["outcome"] == "upstream_error"
    assert completed[0]["status"] == 504


def test_enforce_stream_upstream_error_maps_to_502(tmp_path):
    """A mid-stream upstream connection error is a 502."""
    config = make_test_config(tmp_path, mode="enforce")
    chunks = sse_chunks(["start", "never arrives"])
    app, _config = _stream_app(
        tmp_path,
        chunks,
        config=config,
        stream=ErrorStream(chunks[:1], httpx.ConnectError("connection reset")),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions", content=_stream_body(),
            headers={**auth_headers(), "content-type": "application/json"},
        )
    assert response.status_code == 502
    completed = events_of_type(read_events(_config.events.path), "request_completed")
    assert completed[0]["outcome"] == "upstream_error"
    assert completed[0]["status"] == 502


def test_monitor_stream_upstream_error_records_and_stops(tmp_path):
    """Monitor mode: partial chunks relayed, remainder recorded as stream_error."""
    config = make_test_config(tmp_path, mode="monitor")
    chunks = sse_chunks(["partial ", "lost"])
    app, _config = _stream_app(
        tmp_path,
        chunks,
        config=config,
        stream=ErrorStream(chunks[:1], httpx.ReadError("upstream dropped")),
    )
    with TestClient(app) as client:
        received = []
        with client.stream(
            "POST", "/v1/chat/completions", content=_stream_body(),
            headers={**auth_headers(), "content-type": "application/json"},
        ) as response:
            assert response.status_code == 200
            for chunk in response.iter_bytes():
                received.append(chunk)
    # Only the chunk the upstream actually produced reached the client.
    assert b"".join(received) == chunks[0]
    events = read_events(_config.events.path)
    gaps = events_of_type(events, "uninspected_content")
    assert [gap["kind"] for gap in gaps] == ["stream_error"]
    completed = events_of_type(events, "request_completed")
    assert completed[0]["outcome"] == "upstream_error"
    assert completed[0]["status"] == 200  # the stream did start cleanly
