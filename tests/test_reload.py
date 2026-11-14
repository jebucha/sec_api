"""Hot reload: file watching, failed reload, in-flight ruleset capture (D8).

Covers T-DETECTION-RULES-014..016.
"""

from __future__ import annotations

import shutil
import threading
import time

import httpx
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


def _wait_for(events_path, predicate, timeout=8.0):
    """Poll the events file until an event matches; return the event."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event in read_events(events_path):
            if predicate(event):
                return event
        time.sleep(0.05)
    raise AssertionError("timed out waiting for the expected event")


def _trigger_body(text):
    return {**CHAT_BODY, "messages": [{"role": "user", "content": text}]}


def test_file_edit_reloads_and_emits_ruleset_loaded(tmp_path):
    """T-DETECTION-RULES-014: editing a rule file swaps the active rules."""
    config = make_test_config(tmp_path)
    rules_dir = ensure_rules_dir(config)
    write_rule_file(rules_dir, "pack.jsonl", [rule_dict(keywords=["trigger phrase"])])
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        first = client.post(
            "/v1/chat/completions", json=_trigger_body("a trigger phrase"), headers=auth_headers()
        )
        assert first.status_code == 200
        old_hash = events_of_type(read_events(config.events.path), "verdict")[-1]["ruleset_hash"]

        # Edit the pack: a new rule, a different id and trigger.
        write_rule_file(
            rules_dir, "pack.jsonl", [rule_dict(id="T-RULE2", keywords=["other phrase"])]
        )
        loaded = _wait_for(
            config.events.path,
            lambda e: e["event_type"] == "ruleset_loaded" and e["ruleset_hash"] != old_hash,
        )
        assert loaded["accepted"] == 1
        assert loaded["rejected"] == 0
        new_hash = loaded["ruleset_hash"]

        # Old trigger no longer matches; the new one does.
        verdicts_before = len(events_of_type(read_events(config.events.path), "verdict"))
        stale = client.post(
            "/v1/chat/completions", json=_trigger_body("a trigger phrase"), headers=auth_headers()
        )
        assert stale.status_code == 200
        assert len(events_of_type(read_events(config.events.path), "verdict")) == verdicts_before

        fresh = client.post(
            "/v1/chat/completions", json=_trigger_body("an other phrase"), headers=auth_headers()
        )
        assert fresh.status_code == 200

    verdicts = events_of_type(read_events(config.events.path), "verdict")
    assert verdicts[-1]["ruleset_hash"] == new_hash
    assert verdicts[-1]["matches"][0]["rule_id"] == "T-RULE2"


def test_failed_reload_keeps_previous_ruleset(tmp_path):
    """T-DETECTION-RULES-015: a whole-reload failure keeps the old rules active."""
    config = make_test_config(tmp_path)
    rules_dir = ensure_rules_dir(config)
    write_rule_file(rules_dir, "pack.jsonl", [rule_dict(keywords=["trigger phrase"])])
    records: list[httpx.Request] = []
    app = create_app(config, upstream_transport=httpx.MockTransport(make_upstream_handler(records)))
    with TestClient(app) as client:
        before = client.post(
            "/v1/chat/completions", json=_trigger_body("a trigger phrase"), headers=auth_headers()
        )
        assert before.status_code == 200
        old_hash = events_of_type(read_events(config.events.path), "verdict")[-1]["ruleset_hash"]

        # The rules directory disappears: the reload fails as a whole.
        shutil.rmtree(rules_dir)
        app.state.watcher.reload_now()

        events = read_events(config.events.path)
        failed = events_of_type(events, "ruleset_reload_failed")
        assert len(failed) == 1
        assert failed[0]["ruleset_hash"] == old_hash
        assert "cannot be read" in failed[0]["reason"]
        assert str(rules_dir) in failed[0]["reason"]

        # The previous ruleset is still active: the old rule still matches.
        after = client.post(
            "/v1/chat/completions", json=_trigger_body("a trigger phrase"), headers=auth_headers()
        )
        assert after.status_code == 200
        verdicts = events_of_type(read_events(config.events.path), "verdict")
        assert verdicts[-1]["ruleset_hash"] == old_hash


def test_in_flight_request_keeps_original_hash(tmp_path):
    """T-DETECTION-RULES-016: a request in flight keeps its start-time hash."""
    config = make_test_config(tmp_path)
    rules_dir = ensure_rules_dir(config)
    write_rule_file(rules_dir, "pack.jsonl", [rule_dict(keywords=["trigger phrase"])])

    release = threading.Event()  # set once the reload has happened
    hold = threading.Event()  # set just before the in-flight request is sent

    def handler(request: httpx.Request) -> httpx.Response:
        if hold.is_set():
            release.wait(timeout=10)  # hold this (in-flight) request upstream
        return httpx.Response(200, content=b"{}")

    app = create_app(config, upstream_transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        baseline = client.post(
            "/v1/chat/completions", json=_trigger_body("a trigger phrase"), headers=auth_headers()
        )
        assert baseline.status_code == 200
        old_hash = events_of_type(read_events(config.events.path), "verdict")[-1]["ruleset_hash"]

        result: dict = {}

        def do_request() -> None:
            result["resp"] = client.post(
                "/v1/chat/completions",
                json=_trigger_body("a trigger phrase"),
                headers=auth_headers(),
            )

        hold.set()
        worker = threading.Thread(target=do_request)
        worker.start()
        time.sleep(0.4)  # the request is now in flight, held in the upstream

        # Reload a different ruleset while the request is still processing.
        write_rule_file(
            rules_dir, "pack.jsonl", [rule_dict(id="T-RULE2", keywords=["other phrase"])]
        )
        app.state.watcher.reload_now()
        release.set()
        worker.join(timeout=10)
        assert result["resp"].status_code == 200

        in_flight_id = result["resp"].headers["x-gateway-request-id"]
        in_flight = [
            e
            for e in read_events(config.events.path)
            if e.get("request_id") == in_flight_id
        ]
        assert in_flight, "no events for the in-flight request"
        assert all(e["ruleset_hash"] == old_hash for e in in_flight)

        # A new request after the reload carries the new hash.
        fresh = client.post(
            "/v1/chat/completions", json=_trigger_body("an other phrase"), headers=auth_headers()
        )
        assert fresh.status_code == 200
        fresh_id = fresh.headers["x-gateway-request-id"]
        fresh_events = [
            e for e in read_events(config.events.path) if e.get("request_id") == fresh_id
        ]
        assert fresh_events and fresh_events[0]["ruleset_hash"] != old_hash
