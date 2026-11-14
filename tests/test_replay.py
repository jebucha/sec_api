"""Replay tool: offline metrics, live correlation, corpus validation, thresholds.

Covers T-DETECTION-TESTING-001..004, -005..008, -010.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import httpx
from starlette.testclient import TestClient

from helpers import (
    TEST_TOKEN,
    auth_headers,
    ensure_rules_dir,
    make_test_config,
    make_upstream_handler,
    rule_dict,
    write_rule_file,
)
from llmgw.app import create_app
from llmgw.replay.cli import main, run_replay


def _write_corpus(
    tmp_path: Path, entries: list[dict] | list[str], filename: str = "corpus.jsonl"
) -> Path:
    path = tmp_path / filename
    lines = []
    for entry in entries:
        lines.append(json.dumps(entry, ensure_ascii=False) if isinstance(entry, dict) else entry)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _rules_dir(tmp_path: Path) -> Path:
    rules_dir = tmp_path / "rules"
    write_rule_file(rules_dir, "pack.jsonl", [rule_dict(id="PI-001", keywords=["boom"])])
    return rules_dir


def test_invalid_corpus_line_reported_and_excluded(tmp_path):
    """T-DETECTION-TESTING-001"""
    rules_dir = _rules_dir(tmp_path)
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "id": "c1",
                "text": "boom",
                "label": "malicious",
                "category": "demo",
                "expected_rules": ["PI-001"],
            },
            {"id": "c2", "text": "boom", "category": "demo"},  # missing label
            {"id": "c3", "text": "quiet", "label": "benign", "category": "demo"},
        ],
    )
    report = run_replay(corpus, rules_dir)
    assert len(report.invalid_lines) == 1
    invalid = report.invalid_lines[0]
    assert invalid.line == 2
    assert "label" in invalid.reason
    # The invalid line is excluded from the metrics.
    assert report.entries == 2
    assert sum(report.overall[key] for key in ("tp", "fn", "fp", "tn")) == 2


def test_offline_run_makes_no_network_calls(tmp_path, monkeypatch):
    """T-DETECTION-TESTING-002"""
    rules_dir = _rules_dir(tmp_path)
    corpus = _write_corpus(
        tmp_path, [{"id": "c1", "text": "boom", "label": "malicious", "category": "demo"}]
    )

    def no_socket(*args, **kwargs):
        raise AssertionError("offline replay must not touch the network")

    monkeypatch.setattr(socket, "socket", no_socket)
    exit_code = main(["--corpus", str(corpus), "--rules", str(rules_dir)])
    assert exit_code == 0


def test_response_stage_entry_evaluates_response_rules_only(tmp_path):
    """T-DETECTION-TESTING-003"""
    rules_dir = tmp_path / "rules"
    write_rule_file(
        rules_dir, "req.jsonl", [rule_dict(id="REQ-1", keywords=["trigger"], stage="request")]
    )
    write_rule_file(
        rules_dir,
        "res.jsonl",
        [rule_dict(id="RES-1", keywords=["trigger"], stage="response", target=["assistant_text"])],
    )
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "id": "r1",
                "text": "trigger here",
                "label": "malicious",
                "category": "demo",
                "field": "assistant_text",
                "stage": "response",
                "expected_rules": ["RES-1"],
            },
            {"id": "b1", "text": "clean", "label": "benign", "category": "demo"},
        ],
    )
    report = run_replay(corpus, rules_dir)
    assert report.by_rule["RES-1"]["tp"] == 1
    assert report.by_rule["REQ-1"]["tp"] == 0
    assert report.by_rule["REQ-1"]["fp"] == 0
    assert report.by_rule["REQ-1"]["tn"] == 1
    assert report.overall["tp"] == 1


def test_metrics_match_known_counts(tmp_path):
    """T-DETECTION-TESTING-005"""
    rules_dir = _rules_dir(tmp_path)
    corpus = _write_corpus(
        tmp_path,
        [
            {"id": "m1", "text": "boom one", "label": "malicious", "category": "demo"},
            {"id": "m2", "text": "boom two", "label": "malicious", "category": "demo"},
            {"id": "m3", "text": "quiet malicious", "label": "malicious", "category": "demo"},
            {"id": "b1", "text": "boom benign", "label": "benign", "category": "demo"},
            {"id": "b2", "text": "calm", "label": "benign", "category": "demo"},
            {"id": "b3", "text": "calm again", "label": "benign", "category": "demo"},
            {"id": "b4", "text": "still calm", "label": "benign", "category": "demo"},
        ],
    )
    report = run_replay(corpus, rules_dir)
    overall = report.overall
    assert (overall["tp"], overall["fn"], overall["fp"], overall["tn"]) == (2, 1, 1, 3)
    assert overall["detection_rate"] == 0.667
    assert overall["fp_rate"] == 0.25


def test_benign_match_counted_as_false_positive(tmp_path):
    """T-DETECTION-TESTING-006"""
    rules_dir = _rules_dir(tmp_path)
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "id": "benign-boom",
                "text": "the boom is scheduled",
                "label": "benign",
                "category": "demo",
            }
        ],
    )
    report = run_replay(corpus, rules_dir)
    counts = report.by_rule["PI-001"]
    assert counts["fp"] == 1
    assert counts["false_positive_entries"] == ["benign-boom"]
    assert report.overall["fp"] == 1


def test_expected_rule_miss_listed(tmp_path):
    """T-DETECTION-TESTING-007"""
    rules_dir = _rules_dir(tmp_path)
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "id": "m1",
                "text": "no trigger here",
                "label": "malicious",
                "category": "demo",
                "expected_rules": ["PI-001"],
            }
        ],
    )
    report = run_replay(corpus, rules_dir)
    counts = report.by_rule["PI-001"]
    assert counts["fn"] == 1
    assert counts["expected_misses"] == ["m1"]


def test_fp_threshold_violation_exits_non_zero(tmp_path):
    """T-DETECTION-TESTING-008"""
    rules_dir = _rules_dir(tmp_path)
    corpus = _write_corpus(
        tmp_path,
        [
            {"id": "b1", "text": "boom", "label": "benign", "category": "demo"},
            {"id": "b2", "text": "calm", "label": "benign", "category": "demo"},
        ],
    )
    # FP rate 0.5 exceeds the maximum -> non-zero exit.
    assert main(["--corpus", str(corpus), "--rules", str(rules_dir), "--max-fp-rate", "0.25"]) != 0
    # FP rate 0.5 meets the maximum -> zero exit.
    assert main(["--corpus", str(corpus), "--rules", str(rules_dir), "--max-fp-rate", "0.5"]) == 0


def test_live_mode_correlates_by_request_id_in_monitor_mode(tmp_path):
    """T-DETECTION-TESTING-004"""
    config = make_test_config(tmp_path, mode="monitor")
    rules_dir = ensure_rules_dir(config)
    write_rule_file(
        rules_dir, "pack.jsonl", [rule_dict(id="R-BOOM", keywords=["boom"], action="flag")]
    )
    upstream: list[httpx.Request] = []
    transport = httpx.MockTransport(make_upstream_handler(upstream))
    app = create_app(config, upstream_transport=transport)
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "id": "m1",
                "text": "boom attack",
                "label": "malicious",
                "category": "demo",
                "expected_rules": ["R-BOOM"],
            },
            {"id": "b1", "text": "clean text", "label": "benign", "category": "demo"},
            {
                "id": "r1",
                "text": "boom attack",
                "label": "malicious",
                "category": "demo",
                "stage": "response",
                "expected_rules": ["R-BOOM"],
            },
        ],
        filename="live.jsonl",
    )
    with TestClient(app) as client:
        # A pre-run request leaves a verdict in the events file; naive counting
        # of verdict events would attribute it to the replay entries.
        pre = client.post(
            "/v1/chat/completions",
            json={"model": "qwen3-32b", "messages": [{"role": "user", "content": "boom pre"}]},
            headers=auth_headers(),
        )
        assert pre.status_code == 200
        report = run_replay(
            corpus,
            rules_dir,
            live_url="http://testserver",
            token=TEST_TOKEN,
            model="qwen3-32b",
            events_path=config.events.path,
            client=client,
        )
    assert report.mode == "live"
    # Response-stage entries are reported as skipped and excluded from metrics.
    assert report.skipped == ("r1",)
    assert report.entries == 3
    assert report.evaluated == 2
    # m1's verdict comes from the events whose request_id equals its response
    # header; the pre-run verdict is not attributed to b1 (no false positive),
    # and skipped r1 is not counted as a false negative.
    counts = report.by_rule["R-BOOM"]
    assert (counts["tp"], counts["fn"], counts["fp"], counts["tn"]) == (1, 0, 0, 1)
    assert report.overall["tp"] == 1
    assert report.overall["fn"] == 0
    assert report.overall["fp"] == 0
    assert report.overall["tn"] == 1
    # m1 and b1 were sent as real requests through the gateway; r1 was skipped.
    assert len(upstream) == 3


def test_unlisted_malicious_match_reported_as_unexpected(tmp_path):
    """T-DETECTION-TESTING-010"""
    rules_dir = tmp_path / "rules"
    write_rule_file(
        rules_dir,
        "pack.jsonl",
        [
            rule_dict(id="PI-001", keywords=["alpha"]),
            rule_dict(id="PI-002", keywords=["beta"]),
        ],
    )
    corpus = _write_corpus(
        tmp_path,
        [
            {
                "id": "m1",
                "text": "alpha and beta",
                "label": "malicious",
                "category": "demo",
                "expected_rules": ["PI-002"],
            },
            {"id": "b1", "text": "calm", "label": "benign", "category": "demo"},
        ],
    )
    report = run_replay(corpus, rules_dir)
    pi001 = report.by_rule["PI-001"]
    assert (pi001["tp"], pi001["fn"], pi001["fp"], pi001["tn"]) == (0, 0, 0, 1)
    assert pi001["unexpected_matches"] == ["m1"]
    assert report.by_rule["PI-002"]["tp"] == 1
