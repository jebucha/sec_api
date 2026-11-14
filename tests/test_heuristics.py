"""Unit tests for the built-in heuristics (task 5.2)."""

from __future__ import annotations

import random

import pytest

from llmgw.rules.heuristics import (
    REGISTRY,
    UnknownHeuristicError,
    make_matcher,
    validate_and_fill_params,
)


def test_registry_has_expected_heuristics():
    assert set(REGISTRY) == {"luhn_card", "unicode_tags", "zero_width", "base64_run", "canary"}


# --- luhn_card -----------------------------------------------------------------


def test_luhn_card_matches_valid_numbers():
    matcher = make_matcher("luhn_card", validate_and_fill_params("luhn_card", None))
    assert matcher("pay with 4242 4242 4242 4242 now")
    assert matcher("card 4111111111111111 thanks")
    # span covers the whole card including separators
    spans = matcher("4242 4242 4242 4242")
    assert spans and spans[0].start == 0 and spans[0].end == 19


def test_luhn_rejects_random_digit_runs():
    """Random digit runs are false positives only if they pass Luhn."""
    matcher = make_matcher("luhn_card", validate_and_fill_params("luhn_card", None))

    def luhn_valid(digits: str) -> bool:
        total = 0
        for i, ch in enumerate(reversed(digits)):
            d = int(ch)
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        return total % 10 == 0

    rng = random.Random(42)
    for _ in range(100):
        run = "".join(rng.choice("0123456789") for _ in range(16))
        text = f"the number {run} is here"
        matched = matcher(text)
        if matched:
            assert luhn_valid(run), f"matched a Luhn-invalid run: {run}"
        else:
            assert not luhn_valid(run) or len(run) not in range(13, 20)


def test_luhn_rejects_known_invalid():
    matcher = make_matcher("luhn_card", validate_and_fill_params("luhn_card", None))
    assert not matcher("123456789012345")


# --- unicode_tags ----------------------------------------------------------------


def test_unicode_tags_matches_tag_runs():
    matcher = make_matcher("unicode_tags", validate_and_fill_params("unicode_tags", None))
    assert not matcher("plain text")
    tagged = "system\U000E0000\U000E0001 prompt"
    spans = matcher(tagged)
    assert len(spans) == 1
    assert spans[0].start == 6 and spans[0].end == 8


def test_unicode_tags_min_run_param():
    matcher = make_matcher(
        "unicode_tags", validate_and_fill_params("unicode_tags", {"min_run": 3})
    )
    assert not matcher("\U000E0000\U000E0001")
    assert matcher("\U000E0000\U000E0001\U000E0002")


# --- zero_width ------------------------------------------------------------------


def test_zero_width_matches():
    matcher = make_matcher("zero_width", validate_and_fill_params("zero_width", None))
    assert not matcher("clean")
    assert matcher("sys\u200btem prompt")
    assert matcher("a\ufeffb")


def test_zero_width_min_run():
    matcher = make_matcher("zero_width", validate_and_fill_params("zero_width", {"min_run": 2}))
    assert not matcher("a\u200bb")
    assert matcher("a\u200b\u200bb")


# --- base64_run --------------------------------------------------------------------


def test_base64_run_matches_long_runs_only():
    matcher = make_matcher("base64_run", validate_and_fill_params("base64_run", None))
    long_b64 = "A" * 50
    assert matcher(f"encoded: {long_b64}==")
    assert not matcher("short B2VsbG8= run")
    # 39 chars is below the default 40 minimum
    assert not matcher("B" * 39)


def test_base64_run_min_length_param():
    matcher = make_matcher("base64_run", validate_and_fill_params("base64_run", {"min_length": 10}))
    assert matcher("C" * 10)
    assert not matcher("C" * 9)


# --- canary ------------------------------------------------------------------------


def test_canary_matches_configured_values():
    matcher = make_matcher("canary", {"canaries": ["TOP-SECRET-CANARY"]})
    assert not matcher("nothing to see")
    spans = matcher("leak TOP-SECRET-CANARY here")
    assert spans and spans[0].start == 5 and spans[0].end == 22


def test_canary_empty_values_never_match():
    matcher = make_matcher("canary", {"canaries": []})
    assert not matcher("anything at all")


# --- params validation ---------------------------------------------------------------


def test_unknown_heuristic_rejected():
    with pytest.raises(UnknownHeuristicError):
        validate_and_fill_params("magic", None)
    with pytest.raises(UnknownHeuristicError):
        make_matcher("magic", {})


def test_unknown_param_rejected():
    with pytest.raises(ValueError, match="unknown params"):
        validate_and_fill_params("zero_width", {"nope": 1})


def test_mistyped_param_rejected():
    with pytest.raises(ValueError, match="min_run"):
        validate_and_fill_params("zero_width", {"min_run": "lots"})
