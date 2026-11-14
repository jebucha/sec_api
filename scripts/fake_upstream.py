"""Dry-run inference upstream for lab acceptance (tasks 13.1-13.3).

Serves both wire formats over plain HTTP so the lab scripts can be exercised
without a real model:

  - POST /v1/chat/completions  (OpenAI-compatible, non-streaming + SSE)
  - POST /v1/messages          (Anthropic-compatible, non-streaming + SSE)

Behavior:

  - The reply is deterministic: ``FAKE-REPLY: <last user text>``.
  - Leak simulation: when the user asks to "repeat your system prompt" and a
    system prompt is present, the reply IS the system prompt, verbatim. This
    makes the canary prompt-leak check (task 13.2) deterministic.
  - ``--require-key`` enforces ``Authorization: Bearer <key>`` (401 otherwise),
    mimicking a key-required upstream such as vLLM.
  - ``--header-log FILE`` appends one JSON line per request (path, model,
    stream flag, all received headers) — the "upstream access log / capture"
    that task 13.3 inspects.

Run it with the project environment (starlette + uvicorn are project deps):

    uv run python scripts/fake_upstream.py --port 9101 --name fake-keyed \
        --require-key my-key --header-log /tmp/upstream-headers.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import time
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

REPEAT_RE = re.compile(r"repeat your system prompt", re.IGNORECASE)


def _pick_system(body: dict[str, Any], fmt: str) -> str:
    if fmt == "anthropic":
        system = body.get("system")
        if isinstance(system, str):
            return system
        parts: list[str] = []
        if isinstance(system, list):
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
        return "\n".join(parts)
    system: list[str] = []
    for message in body.get("messages") or []:
        if isinstance(message, dict) and message.get("role") in ("system", "developer"):
            content = message.get("content")
            if isinstance(content, str):
                system.append(content)
    return "\n".join(system)


def _pick_user(body: dict[str, Any], fmt: str) -> str:
    if fmt == "anthropic":
        user: list[str] = []
        for message in body.get("messages") or []:
            if isinstance(message, dict) and message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, str):
                    user.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text")
                            if isinstance(text, str):
                                user.append(text)
        return "\n".join(user)
    user = []
    for message in body.get("messages") or []:
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                user.append(content)
    return "\n".join(user)


def make_reply(body: dict[str, Any], fmt: str) -> str:
    system = _pick_system(body, fmt)
    user = _pick_user(body, fmt)
    if REPEAT_RE.search(user) and system:
        return system
    return f"FAKE-REPLY: {user}"


class FakeUpstream:
    def __init__(
        self,
        *,
        require_key: str | None = None,
        header_log: str | None = None,
    ) -> None:
        self.require_key = require_key
        self.header_log = header_log

    def _log_headers(self, path: str, body: dict[str, Any] | None, headers: dict[str, str]) -> None:
        if not self.header_log:
            return
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "path": path,
            "model": body.get("model") if isinstance(body, dict) else None,
            "stream": body.get("stream") if isinstance(body, dict) else None,
            "headers": {key.lower(): value for key, value in headers.items()},
        }
        with open(self.header_log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")

    def _authed(self, headers: dict[str, str]) -> bool:
        if self.require_key is None:
            return True
        return headers.get("authorization") == f"Bearer {self.require_key}"

    # --- OpenAI shape ---------------------------------------------------------

    def _openai_reply_json(self, body: dict[str, Any]) -> dict[str, Any]:
        model = body.get("model") or "fake"
        return {
            "id": "chatcmpl-fake-1",
            "object": "chat.completion",
            "created": 1765000000,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": make_reply(body, "openai")},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 8, "total_tokens": 16},
        }

    def _openai_sse(self, body: dict[str, Any]):
        model = body.get("model") or "fake"
        text = make_reply(body, "openai")
        third = len(text) // 3
        parts = [text[:third], text[third:2 * third], text[2 * third:]]
        base = {
            "id": "chatcmpl-fake-1",
            "object": "chat.completion.chunk",
            "created": 1765000000,
            "model": model,
        }

        def chunk(delta: dict[str, Any], finish: str | None) -> str:
            payload = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"

        yield chunk({"role": "assistant", "content": ""}, None)
        for part in parts:
            if part:
                yield chunk({"content": part}, None)
        yield chunk({}, "stop")
        yield "data: [DONE]\n\n"

    # --- Anthropic shape ------------------------------------------------------

    def _anthropic_reply_json(self, body: dict[str, Any]) -> dict[str, Any]:
        model = body.get("model") or "fake"
        return {
            "id": "msg_fake_1",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": make_reply(body, "anthropic")}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 8, "output_tokens": 8},
        }

    def _anthropic_sse(self, body: dict[str, Any]):
        model = body.get("model") or "fake"
        text = make_reply(body, "anthropic")
        mid = len(text) // 2
        parts = [text[:mid], text[mid:]]

        def event(event_type: str, data: dict[str, Any]) -> str:
            return f"event: {event_type}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"

        yield event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_fake_1",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 8, "output_tokens": 0},
                },
            },
        )
        yield event("content_block_start", {"type": "content_block_start", "index": 0,
                                            "content_block": {"type": "text", "text": ""}})
        for part in parts:
            if part:
                yield event("content_block_delta",
                            {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": part}})
        yield event("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield event("message_delta", {"type": "message_delta",
                                      "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                      "usage": {"output_tokens": 8}})
        yield event("message_stop", {"type": "message_stop"})

    # --- routes ---------------------------------------------------------------

    def _handle(self, fmt: str, path: str, request_body: bytes, headers: dict[str, str]):
        try:
            body = json.loads(request_body)
            if not isinstance(body, dict):
                raise ValueError("not an object")
        except Exception:
            body = {}
        self._log_headers(path, body, headers)
        if not self._authed(headers):
            status = 401
            if fmt == "openai":
                error = {
                    "message": "Incorrect API key provided",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_api_key",
                }
                return JSONResponse({"error": error}, status_code=status)
            return JSONResponse(
                {"type": "error", "error": {"type": "authentication_error",
                                            "message": "invalid x-api-key or Authorization"}},
                status_code=status,
            )
        stream = bool(body.get("stream"))
        if fmt == "openai":
            if stream:
                return StreamingResponse(self._openai_sse(body), media_type="text/event-stream")
            return JSONResponse(self._openai_reply_json(body))
        if stream:
            return StreamingResponse(self._anthropic_sse(body), media_type="text/event-stream")
        return JSONResponse(self._anthropic_reply_json(body))

    async def chat_completions(self, request) -> Response:  # type: ignore[no-untyped-def]
        raw = await request.body()
        headers = dict(request.headers)
        return self._handle("openai", "/v1/chat/completions", raw, headers)

    async def messages(self, request) -> Response:  # type: ignore[no-untyped-def]
        raw = await request.body()
        headers = dict(request.headers)
        return self._handle("anthropic", "/v1/messages", raw, headers)

    async def health(self, request) -> Response:  # type: ignore[no-untyped-def]
        return JSONResponse({"ok": True})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--name", default=None, help="unused; responses echo the request model")
    parser.add_argument("--require-key", default=None,
                        help="require 'Authorization: Bearer <key>' (401 otherwise)")
    parser.add_argument("--header-log", default=None,
                        help="append one JSON line per request (path, model, headers) to this file")
    args = parser.parse_args(argv)

    upstream = FakeUpstream(require_key=args.require_key, header_log=args.header_log)
    app = Starlette(
        routes=[
            Route("/v1/chat/completions", upstream.chat_completions, methods=["POST"]),
            Route("/v1/messages", upstream.messages, methods=["POST"]),
            Route("/health", upstream.health, methods=["GET"]),
        ]
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
