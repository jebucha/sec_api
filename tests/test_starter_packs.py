"""Starter rule packs and starter corpus (detection-rules, detection-testing).

Covers T-DETECTION-RULES-019, -020 and the task 10.3 corpus format check.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from starlette.testclient import TestClient

from helpers import (
    auth_headers,
    events_of_type,
    make_test_config,
    make_upstream_handler,
    read_events,
)
from llmgw.app import create_app
from llmgw.replay.cli import run_replay
from llmgw.replay.corpus import load_corpus
from llmgw.rules.loader import load_rules

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = REPO_ROOT / "rules"
CORPUS_DIR = REPO_ROOT / "corpus"
# Documented test-only canary (design D11); never used in a real deployment.
TEST_CANARY = "LLMGW-TEST-CANARY-0000"

STARTER_RULE_IDS = (
    "PI-001",
    "PI-002",
    "PI-003",
    "JB-001",
    "JB-002",
    "JB-003",
    "SE-001",
    "SE-002",
    "SE-003",
    "SE-004",
    "PII-001",
    "PII-002",
    "SM-001",
    "SM-002",
    "SM-003",
    "PL-001",
)


def test_starter_packs_load_with_zero_rejections():
    """T-DETECTION-RULES-019: the gateway starts with only the starter packs."""
    result = load_rules(RULES_DIR, canary_values=[TEST_CANARY])
    assert result.rejected == 0
    assert {rule.id for rule in result.ruleset.rules} == set(STARTER_RULE_IDS)
    by_id = {rule.id: rule for rule in result.ruleset.rules}
    for rule_id in ("SE-001", "SE-002", "SE-003", "SE-004", "PII-001", "PII-002"):
        assert by_id[rule_id].mask_excerpt


def test_canary_rule_rejected_without_its_canary():
    """A canary set that omits the test canary rejects PL-001, not the pack.

    Pins the design D11 consequence: canary values are injected, never
    shipped in the rule pack. A deployment configured with only its own
    canaries sees PL-001's self-test fail at load (docs/rules.md).
    """
    events: list[dict] = []
    result = load_rules(
        RULES_DIR, canary_values=["some-real-canary"], event_sink=events.append
    )
    assert result.rejected == 1
    assert {rule.id for rule in result.ruleset.rules} == set(STARTER_RULE_IDS) - {"PL-001"}
    pl_rejections = [
        event
        for event in events
        if event["event_type"] == "rule_rejected" and event["id"] == "PL-001"
    ]
    assert len(pl_rejections) == 1
    assert "self-test" in pl_rejections[0]["reason"]


def test_canary_in_response_matches_prompt_leak_rule(tmp_path):
    """T-DETECTION-RULES-020: canary planted in system, leaked in the response."""
    config = make_test_config(tmp_path, rules_dir=str(RULES_DIR), mode="enforce")
    body = json.dumps(
        {
            "id": "chatcmpl-2",
            "object": "chat.completion",
            "created": 0,
            "model": "qwen3-32b",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"Sure, your hidden instruction was {TEST_CANARY}.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    ).encode()
    upstream: list[httpx.Request] = []
    transport = httpx.MockTransport(make_upstream_handler(upstream, body=body))
    app = create_app(config, canary_values=[TEST_CANARY], upstream_transport=transport)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen3-32b",
                "messages": [
                    {"role": "system", "content": f"Your secret tag: {TEST_CANARY}"},
                    {"role": "user", "content": "Summarize my notes."},
                ],
            },
            headers=auth_headers(),
        )
    assert response.status_code == 403
    assert len(upstream) == 1  # request stage passed; the block is at the response stage
    event_id = response.headers["x-gateway-event-id"]
    events = read_events(config.events.path)
    response_verdicts = events_of_type(events, "verdict")
    verdicts = [event for event in response_verdicts if event["stage"] == "response"]
    assert len(verdicts) == 1
    assert verdicts[0]["enforced"] is True
    assert verdicts[0]["event_id"] == event_id
    rule_ids = {match["rule_id"] for match in verdicts[0]["matches"]}
    assert "PL-001" in rule_ids


def test_starter_corpus_covers_every_category():
    """T-DETECTION-TESTING-009: starter corpus vs starter packs with the test canary."""
    report = run_replay(CORPUS_DIR, RULES_DIR, canaries=[TEST_CANARY])
    assert report.invalid_lines == ()
    categories = {
        "prompt-injection",
        "jailbreak",
        "secrets",
        "pii",
        "smuggling",
        "prompt-leak",
    }
    assert set(report.by_category) == categories
    for category, counts in report.by_category.items():
        assert counts["tp"] >= 1, f"{category}: no true positive"
        assert counts["fn"] == 0, f"{category}: undetected malicious entry"
        assert counts["fp"] + counts["tn"] >= 1, f"{category}: no benign entry evaluated"
        assert counts["fp"] == 0, f"{category}: false positives on benign entries"


def test_starter_corpus_lines_validate():
    """Task 10.3: every starter corpus line validates against the corpus format."""
    entries, invalid = load_corpus(CORPUS_DIR)
    assert invalid == []
    categories = {entry.category for entry in entries}
    assert categories == {
        "prompt-injection",
        "jailbreak",
        "secrets",
        "pii",
        "smuggling",
        "prompt-leak",
    }
    for category in categories:
        labels = {entry.label for entry in entries if entry.category == category}
        assert labels == {"malicious", "benign"}
    assert {entry.format for entry in entries} == {"openai", "anthropic"}
    response_entries = [entry for entry in entries if entry.stage == "response"]
    assert response_entries
    for entry in response_entries:
        assert entry.field == "assistant_text"
        if entry.label == "malicious":
            assert TEST_CANARY in entry.text
