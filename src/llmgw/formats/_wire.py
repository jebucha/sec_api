"""Shared format-parsing helpers: duplicate-key detection and SSE blocks."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass


class FormatError(Exception):
    """The body is not valid for this wire format."""


class DuplicateKeyError(FormatError):
    """The JSON body contains a duplicate key (top level or in a message)."""

    def __init__(self, key: str) -> None:
        super().__init__(f"duplicate JSON key: {key!r}")
        self.key = key


def parse_json_strict(body: bytes) -> object:
    """Parse JSON, raising DuplicateKeyError on any duplicate key."""

    def hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        seen: set[str] = set()
        for key, _value in pairs:
            if key in seen:
                raise DuplicateKeyError(key)
            seen.add(key)
        return dict(pairs)

    return json.loads(body, object_pairs_hook=hook)


def parse_json_lenient(body: bytes) -> object:
    """Parse JSON allowing duplicate keys (last wins), for response bodies."""
    return json.loads(body)


def json_dumps(data: object) -> bytes:
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


@dataclass
class SseBlock:
    event: str | None  # the ``event:`` name, if the block had one
    data: str  # data lines joined by newlines


def parse_sse_blocks(raw: bytes) -> list[SseBlock]:
    """Parse raw SSE bytes into blocks.

    Handles CRLF, LF, and CR line endings. Chunk boundaries are irrelevant:
    the parser works on the fully accumulated byte string, so SSE lines split
    across upstream chunks are reassembled.
    """
    text = raw.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[SseBlock] = []
    for chunk in text.split("\n\n"):
        event: str | None = None
        data_lines: list[str] = []
        for line in chunk.split("\n"):
            if not line or line.startswith(":"):
                continue
            if ":" in line:
                field, _, value = line.partition(":")
                if value.startswith(" "):
                    value = value[1:]
            else:
                field, value = line, ""
            if field == "event" and event is None:
                event = value
            elif field == "data":
                data_lines.append(value)
        if event is not None or data_lines:
            blocks.append(SseBlock(event=event, data="\n".join(data_lines)))
    return blocks


def iter_sse_json(raw: bytes) -> Iterable[tuple[str | None, object | None]]:
    """Yield (event name, parsed JSON data) for each data block in an SSE stream."""
    for block in parse_sse_blocks(raw):
        data: object = None
        if block.data:
            try:
                data = json.loads(block.data)
            except json.JSONDecodeError:
                data = None
        yield block.event, data
