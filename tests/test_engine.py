"""Tests for the detection engine (detection-engine spec)."""

from __future__ import annotations

import json

from helpers import make_rule, make_ruleset
from llmgw.engine import evaluate
from llmgw.events import RequestContext, rule_error_event, verdict_event
from llmgw.formats.canonical import Canonical
from llmgw.rules.loader import load_rules


def _canon(**fields) -> Canonical:
    return Canonical(**fields)


def test_rule_only_evaluates_its_target_fields():
    """T-DETECTION-ENGINE-002"""
    ruleset = make_ruleset(make_rule(rule_id="SYS-1", target=("system",)))
    verdict = evaluate(
        ruleset,
        _canon(system=("benign system",), user_text=("trigger phrase here",)),
        stage="request",
        mode="enforce",
    )
    assert verdict.action == "allow"
    assert verdict.matches == ()


def test_all_matching_rules_listed_in_verdict():
    """T-DETECTION-ENGINE-003"""
    ruleset = make_ruleset(
        make_rule(rule_id="F-1", matches="alpha", action="flag"),
        make_rule(rule_id="B-1", matches="beta", action="block"),
    )
    verdict = evaluate(
        ruleset,
        _canon(user_text=("alpha and beta together",)),
        stage="request",
        mode="enforce",
    )
    assert {m.rule_id for m in verdict.matches} == {"F-1", "B-1"}
    assert verdict.action == "block"
    assert verdict.blocked


def test_flag_only_match_resolves_to_flag():
    """T-DETECTION-ENGINE-004"""
    ruleset = make_ruleset(make_rule(matches="flaggy", action="flag"))
    verdict = evaluate(
        ruleset,
        _canon(user_text=("flaggy content",)),
        stage="request",
        mode="enforce",
    )
    assert verdict.action == "flag"
    assert not verdict.blocked
    assert not verdict.enforced


def test_no_match_resolves_to_allow():
    """T-DETECTION-ENGINE-005"""
    ruleset = make_ruleset(make_rule(matches="never appears"))
    verdict = evaluate(
        ruleset,
        _canon(user_text=("quiet content",)),
        stage="request",
        mode="enforce",
    )
    assert verdict.action == "allow"
    assert verdict.matches == ()
    assert not verdict.blocked


def test_log_precedence_below_flag():
    ruleset = make_ruleset(
        make_rule(rule_id="L-1", matches="a", action="log"),
        make_rule(rule_id="F-1", matches="b", action="flag"),
        make_rule(rule_id="B-1", matches="c", action="block"),
    )
    only_log = evaluate(
        ruleset, _canon(user_text=("a only",)), stage="request", mode="enforce"
    )
    assert only_log.action == "log"
    log_and_flag = evaluate(
        ruleset, _canon(user_text=("a and b",)), stage="request", mode="enforce"
    )
    assert log_and_flag.action == "flag"
    all_three = evaluate(
        ruleset, _canon(user_text=("a and b and c",)), stage="request", mode="enforce"
    )
    assert all_three.action == "block"


def test_stage_scoping():
    """A response-stage rule is not evaluated at the request stage."""
    ruleset = make_ruleset(
        make_rule(rule_id="R-1", stage="response", target=("assistant_text",), matches="leak")
    )
    verdict = evaluate(
        ruleset,
        _canon(assistant_text=("the leak is here",)),
        stage="request",
        mode="enforce",
    )
    assert verdict.action == "allow"
    verdict_response = evaluate(
        ruleset,
        _canon(assistant_text=("the leak is here",)),
        stage="response",
        mode="enforce",
    )
    assert verdict_response.action == "flag"


def test_disabled_rule_not_evaluated(tmp_path):
    """T-DETECTION-RULES-011: a disabled rule produces no verdict."""

    def _kw(rule_id: str, word: str, action: str, enabled: bool) -> str:
        return json.dumps(
            {
                "id": rule_id,
                "name": rule_id,
                "version": "1",
                "stage": "request",
                "target": ["user_text"],
                "type": "keyword",
                "severity": "high",
                "action": action,
                "enabled": enabled,
                "keywords": [word],
            }
        )

    (tmp_path / "pack.jsonl").write_text(
        "\n".join(
            [
                _kw("ON", "enabled word", "flag", True),
                _kw("OFF", "disabled word", "block", False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = load_rules(tmp_path)
    assert result.rejected == 0
    verdict = evaluate(
        result.ruleset,
        _canon(user_text=("both enabled word and disabled word present",)),
        stage="request",
        mode="enforce",
    )
    assert [m.rule_id for m in verdict.matches] == ["ON"]
    assert verdict.action == "flag"  # the disabled block rule did not fire


def test_rule_targeting_reasoning_text_matches():
    """T-CONTENT-NORMALIZATION-030: a match only in reasoning content names
    the canonical field ``reasoning_text``."""
    ruleset = make_ruleset(
        make_rule(
            rule_id="RT-1",
            matches="hidden thought",
            target=("reasoning_text",),
            stage="response",
        )
    )
    verdict = evaluate(
        ruleset,
        _canon(
            assistant_text=("visible answer",),
            reasoning_text=("the hidden thought",),
        ),
        stage="response",
        mode="enforce",
    )
    (match,) = verdict.matches
    assert match.rule_id == "RT-1"
    assert match.field == "reasoning_text"
    assert match.segment_index == 0
    assert match.excerpt == "hidden thought"


def test_large_tool_result_match_found():
    """T-CONTENT-NORMALIZATION-023: a 1 MB segment is inspected in full."""
    pattern = "needle-near-the-end"
    segment = "x" * (1024 * 1024 - len(pattern)) + pattern
    ruleset = make_ruleset(make_rule(rule_id="TR-1", matches=pattern, target=("tool_results",)))
    verdict = evaluate(
        ruleset,
        _canon(tool_results=(segment,)),
        stage="request",
        mode="enforce",
    )
    (match,) = verdict.matches
    assert match.field == "tool_results"
    assert match.segment_index == 0
    assert match.excerpt == pattern


def test_long_match_excerpt_truncated():
    """T-DETECTION-ENGINE-008: a 5,000-char match is bounded to the max."""
    long_run = "x" * 5000
    ruleset = make_ruleset(make_rule(matches=long_run))
    verdict = evaluate(
        ruleset,
        _canon(user_text=(f"start {long_run} end",)),
        stage="request",
        mode="enforce",
        excerpt_max_chars=200,
    )
    (match,) = verdict.matches
    assert len(match.excerpt) == 200
    assert match.excerpt == "x" * 200


def test_excerpt_max_chars_configurable():
    long_run = "y" * 50
    ruleset = make_ruleset(make_rule(matches=long_run))
    verdict = evaluate(
        ruleset,
        _canon(user_text=(long_run,)),
        stage="request",
        mode="enforce",
        excerpt_max_chars=10,
    )
    assert verdict.matches[0].excerpt == "y" * 10


def test_masked_secret_excerpt_never_in_events():
    """T-DETECTION-ENGINE-009: masked excerpt, full value absent from events."""
    secret = "AKIAIOSFODNN7EXAMPLE"
    ruleset = make_ruleset(make_rule(matches=secret, mask_excerpt=True))
    verdict = evaluate(
        ruleset,
        _canon(user_text=(f"your key is {secret} ok",)),
        stage="request",
        mode="enforce",
    )
    (match,) = verdict.matches
    assert match.excerpt == "AK\u2026LE (20 chars)"

    ctx = RequestContext(request_id="req-1", client="c", mode="enforce", ruleset_hash="h")
    event = verdict_event(
        ctx,
        stage="request",
        format="openai",
        model="m",
        upstream="u",
        action=verdict.action,
        enforced=verdict.enforced,
        matches=verdict.matches,
    )
    blob = json.dumps(event)
    assert secret not in blob
    (event_match,) = event["matches"]
    assert event_match["excerpt"] == "AK\u2026LE (20 chars)"


def test_short_masked_excerpt_stays_masked():
    ruleset = make_ruleset(make_rule(matches="ab1", mask_excerpt=True))
    verdict = evaluate(
        ruleset,
        _canon(user_text=("code ab1 end",)),
        stage="request",
        mode="enforce",
    )
    assert verdict.matches[0].excerpt == "**** (3 chars)"


def test_rule_exception_blocks_in_enforce_and_emits_rule_error():
    """T-DETECTION-ENGINE-010"""
    ruleset = make_ruleset(make_rule(rule_id="BOOM", raises=RuntimeError("exploded")))
    verdict = evaluate(
        ruleset,
        _canon(user_text=("anything at all",)),
        stage="request",
        mode="enforce",
    )
    assert verdict.action == "block"
    assert verdict.enforced
    assert verdict.blocked
    (error,) = verdict.rule_errors
    assert error.rule_id == "BOOM"
    assert "exploded" in error.error

    ctx = RequestContext(request_id="req-1", client="c", mode="enforce", ruleset_hash="h")
    event = rule_error_event(ctx, rule_id=error.rule_id, error=error.error)
    assert event["event_type"] == "rule_error"
    assert event["rule_id"] == "BOOM"


def test_rule_exception_passes_in_monitor_and_emits_rule_error():
    """T-DETECTION-ENGINE-011"""
    ruleset = make_ruleset(make_rule(rule_id="BOOM", raises=RuntimeError("exploded")))
    verdict = evaluate(
        ruleset,
        _canon(user_text=("anything at all",)),
        stage="request",
        mode="monitor",
    )
    assert verdict.action == "allow"
    assert not verdict.blocked
    (error,) = verdict.rule_errors
    assert error.rule_id == "BOOM"


def test_rule_error_in_enforce_does_not_clobber_clean_allow():
    """No matches + error in enforce: the stage is blocked by the error."""
    ruleset = make_ruleset(make_rule(rule_id="BOOM", raises=ValueError("bad input")))
    verdict = evaluate(
        ruleset, _canon(user_text=("clean",)), stage="request", mode="enforce"
    )
    assert verdict.blocked
    assert verdict.matches == ()
