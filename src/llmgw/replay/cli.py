"""``llmgw-replay``: measure detection rules against the labeled corpus.

Offline mode (default) evaluates corpus entries directly against a rules
directory with the same loading and engine behavior as the gateway — no
running gateway, no upstream, no network. Live mode (``--live``) sends
request-stage entries through a running gateway as real requests in their
``format`` and correlates each response with the events file via the
``X-Gateway-Request-Id`` header; response-stage entries are reported as
skipped (detection-testing: Live replay).

Metric definitions (detection-testing: metric definitions):
  overall / per category: detected = at least one rule matched (any action);
      TP = malicious and detected, FN = malicious and not detected,
      FP = benign and detected, TN = benign and not detected.
  per rule R: positives = malicious entries listing R in expected_rules;
      negatives = all benign entries; a match of R on a malicious entry that
      does not list R is an *unexpected match* (counts unchanged).
  detection rate = TP/(TP+FN), false-positive rate = FP/(FP+TN); a rate with
  a zero denominator is null.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..engine import evaluate
from ..formats.canonical import Canonical
from ..rules.loader import RulesetLoadError, load_rules
from .corpus import CorpusEntry, InvalidLine, load_corpus


def _canonical_for(entry: CorpusEntry) -> Canonical:
    """Place the entry text in its canonical field (detection-testing: offline)."""
    if entry.field == "system":
        return Canonical(system=(entry.text,))
    if entry.field == "assistant_text":
        return Canonical(assistant_text=(entry.text,))
    return Canonical(user_text=(entry.text,))


def _new_counts() -> dict[str, Any]:
    return {"tp": 0, "fn": 0, "fp": 0, "tn": 0}


def _with_rates(counts: dict[str, Any]) -> dict[str, Any]:
    out = dict(counts)
    det_denom = counts["tp"] + counts["fn"]
    fp_denom = counts["fp"] + counts["tn"]
    out["detection_rate"] = round(counts["tp"] / det_denom, 3) if det_denom else None
    out["fp_rate"] = round(counts["fp"] / fp_denom, 3) if fp_denom else None
    return out


def _tally(counts: dict[str, Any], entry: CorpusEntry, detected: bool) -> None:
    if entry.label == "malicious":
        counts["tp" if detected else "fn"] += 1
    else:
        counts["fp" if detected else "tn"] += 1


@dataclass(frozen=True)
class ReplayReport:
    """The full replay result: metrics, listings, and validation output."""

    mode: str
    ruleset_hash: str
    active_rules: int
    canaries: tuple[str, ...]
    entries: int
    evaluated: int
    invalid_lines: tuple[InvalidLine, ...]
    overall: dict[str, Any]
    by_category: dict[str, dict[str, Any]]
    by_rule: dict[str, dict[str, Any]]
    skipped: tuple[str, ...] = field(default_factory=tuple)
    rule_errors: tuple[dict[str, str], ...] = field(default_factory=tuple)
    violations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "ruleset_hash": self.ruleset_hash,
            "active_rules": self.active_rules,
            "canaries": list(self.canaries),
            "entries": self.entries,
            "evaluated": self.evaluated,
            "invalid_lines": [
                {"file": line.file, "line": line.line, "reason": line.reason}
                for line in self.invalid_lines
            ],
            "skipped": list(self.skipped),
            "rule_errors": list(self.rule_errors),
            "overall": self.overall,
            "by_category": self.by_category,
            "by_rule": self.by_rule,
            "violations": list(self.violations),
        }


def _check_thresholds(
    overall: dict[str, Any],
    min_detection_rate: float | None,
    max_fp_rate: float | None,
) -> list[str]:
    violations: list[str] = []
    if min_detection_rate is not None:
        if overall["detection_rate"] is None:
            violations.append(
                f"min_detection_rate={min_detection_rate}: detection rate is null "
                "(no malicious entries)"
            )
        elif overall["detection_rate"] < min_detection_rate:
            violations.append(
                f"min_detection_rate={min_detection_rate}: "
                f"detection rate {overall['detection_rate']}"
            )
    if max_fp_rate is not None:
        if overall["fp_rate"] is None:
            violations.append(
                f"max_fp_rate={max_fp_rate}: false-positive rate is null (no benign entries)"
            )
        elif overall["fp_rate"] > max_fp_rate:
            violations.append(
                f"max_fp_rate={max_fp_rate}: false-positive rate {overall['fp_rate']}"
            )
    return violations


def _compute_metrics(
    entries: list[CorpusEntry],
    matched_by_entry: dict[str, tuple[str, ...]],
    rule_ids: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    overall = _new_counts()
    by_category: dict[str, dict[str, Any]] = {}
    by_rule: dict[str, dict[str, Any]] = {}
    for rule_id in rule_ids:
        counts = _new_counts()
        counts["false_positive_entries"] = []
        counts["expected_misses"] = []
        counts["unexpected_matches"] = []
        by_rule[rule_id] = counts
    for entry in entries:
        matched = matched_by_entry.get(entry.id, ())
        matched_set = set(matched)
        detected = bool(matched)
        _tally(overall, entry, detected)
        _tally(by_category.setdefault(entry.category, _new_counts()), entry, detected)
        expected = set(entry.expected_rules)
        for rule_id, counts in by_rule.items():
            rule_matched = rule_id in matched_set
            if entry.label == "benign":
                if rule_matched:
                    counts["fp"] += 1
                    counts["false_positive_entries"].append(entry.id)
                else:
                    counts["tn"] += 1
            elif rule_id in expected:
                if rule_matched:
                    counts["tp"] += 1
                else:
                    counts["fn"] += 1
                    counts["expected_misses"].append(entry.id)
            elif rule_matched:
                counts["unexpected_matches"].append(entry.id)
    return (
        _with_rates(overall),
        {cat: _with_rates(counts) for cat, counts in sorted(by_category.items())},
        {rule_id: _with_rates(counts) for rule_id, counts in sorted(by_rule.items())},
    )


def _live_messages(entry: CorpusEntry) -> list[dict[str, str]]:
    if entry.field == "system":
        return [
            {"role": "system", "content": entry.text},
            {"role": "user", "content": "Summarize the above."},
        ]
    return [{"role": "user", "content": entry.text}]


def _send_live_entry(
    client: httpx.Client, entry: CorpusEntry, *, token: str, model: str
) -> httpx.Response:
    if entry.format == "openai":
        path, headers = "/v1/chat/completions", {"authorization": f"Bearer {token}"}
    else:
        path, headers = "/v1/messages", {"x-api-key": token}
    body = {"model": model, "messages": _live_messages(entry)}
    return client.post(path, json=body, headers=headers)


def _index_events(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    by_request: dict[str, list[dict[str, Any]]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        request_id = event.get("request_id")
        if request_id is not None:
            by_request.setdefault(request_id, []).append(event)
    return by_request


def run_replay(
    corpus_paths: str | Path | list[str | Path],
    rules_dir: str | Path,
    *,
    canaries: list[str] | None = None,
    min_detection_rate: float | None = None,
    max_fp_rate: float | None = None,
    live_url: str | None = None,
    token: str | None = None,
    model: str | None = None,
    events_path: str | Path | None = None,
    client: httpx.Client | None = None,
) -> ReplayReport:
    """Run the replay; returns the structured report (no stdout, no exit code).

    Raises RulesetLoadError when the rules directory cannot be read.
    """
    entries, invalid = load_corpus(corpus_paths)
    load_result = load_rules(rules_dir, canary_values=canaries or [])
    ruleset = load_result.ruleset
    rule_ids = tuple(rule.id for rule in sorted(ruleset.rules, key=lambda r: r.id))

    matched_by_entry: dict[str, tuple[str, ...]] = {}
    skipped: list[str] = []
    rule_errors: list[dict[str, str]] = []

    if live_url is None:
        for entry in entries:
            verdict = evaluate(
                ruleset, _canonical_for(entry), stage=entry.stage, mode="monitor"
            )
            matched_by_entry[entry.id] = tuple(sorted({m.rule_id for m in verdict.matches}))
            for error in verdict.rule_errors:
                rule_errors.append({"rule_id": error.rule_id, "error": error.error})
    else:
        if token is None or model is None or events_path is None:
            raise ValueError("live mode requires token, model, and events_path")
        own_client = client is None
        live_client = client or httpx.Client(base_url=live_url, timeout=10.0)
        try:
            pending: list[tuple[CorpusEntry, str]] = []
            for entry in entries:
                if entry.stage == "response":
                    skipped.append(entry.id)
                    continue
                if entry.field == "assistant_text":
                    # Assistant text cannot occur in a request; skip it.
                    skipped.append(entry.id)
                    continue
                response = _send_live_entry(live_client, entry, token=token, model=model)
                request_id = response.headers.get("x-gateway-request-id")
                if request_id is None:
                    raise RuntimeError(
                        f"live response for {entry.id!r} has no X-Gateway-Request-Id header"
                    )
                pending.append((entry, request_id))
        finally:
            if own_client:
                live_client.close()
        events_by_request = _index_events(events_path)
        for entry, request_id in pending:
            events = events_by_request.get(request_id, [])
            matched_by_entry[entry.id] = tuple(
                sorted(
                    {
                        match["rule_id"]
                        for event in events
                        if event.get("event_type") == "verdict"
                        for match in event.get("matches", [])
                    }
                )
            )
            rule_errors.extend(
                {"rule_id": event["rule_id"], "error": event["error"]}
                for event in events
                if event.get("event_type") == "rule_error"
            )

    # Skipped entries (live mode) are reported but never evaluated, so they
    # must not contribute to any metric as a TN or FN.
    skipped_ids = set(skipped)
    evaluated_entries = [entry for entry in entries if entry.id not in skipped_ids]
    overall, by_category, by_rule = _compute_metrics(
        evaluated_entries, matched_by_entry, rule_ids
    )
    violations = _check_thresholds(overall, min_detection_rate, max_fp_rate)
    return ReplayReport(
        mode="live" if live_url else "offline",
        ruleset_hash=ruleset.hash,
        active_rules=len(ruleset.rules),
        canaries=tuple(canaries or ()),
        entries=len(entries),
        evaluated=len(evaluated_entries),
        invalid_lines=tuple(invalid),
        overall=overall,
        by_category=by_category,
        by_rule=by_rule,
        skipped=tuple(skipped),
        rule_errors=tuple(rule_errors),
        violations=tuple(violations),
    )


def _fmt_rate_cell(value: float | None) -> str:
    return "null" if value is None else f"{value}"


def _listings_line(label: str, items: list[str]) -> str:
    return f"{label:<24}{', '.join(items) if items else '(none)'}"


def _table_header(label: str, width: int) -> str:
    return (
        f"{label:<{width}} {'tp':>3} {'fn':>3} {'fp':>3} {'tn':>3}"
        f"  {'detection':>9}  {'fp_rate':>7}"
    )


def render_table(report: ReplayReport) -> str:
    lines: list[str] = []
    lines.append(f"llmgw-replay ({report.mode})")
    lines.append(
        f"ruleset: {report.ruleset_hash[:12]}  active rules: {report.active_rules}"
        + (f"  canaries: {', '.join(report.canaries)}" if report.canaries else "")
    )
    lines.append(
        f"entries: {report.entries}  evaluated: {report.evaluated}"
        f"  invalid lines: {len(report.invalid_lines)}"
        + (f"  skipped: {len(report.skipped)}" if report.mode == "live" else "")
    )
    lines.append("")
    o = report.overall
    lines.append(
        f"overall       tp={o['tp']}  fn={o['fn']}  fp={o['fp']}  tn={o['tn']}"
        f"  detection={_fmt_rate_cell(o['detection_rate'])}"
        f"  fp_rate={_fmt_rate_cell(o['fp_rate'])}"
    )
    lines.append("")
    lines.append("by category:")
    lines.append(_table_header("category", 20))
    for category, counts in report.by_category.items():
        lines.append(
            f"{category:<20} {counts['tp']:>3} {counts['fn']:>3} {counts['fp']:>3}"
            f" {counts['tn']:>3}  {_fmt_rate_cell(counts['detection_rate']):>9}"
            f"  {_fmt_rate_cell(counts['fp_rate']):>7}"
        )
    lines.append("")
    lines.append("by rule:")
    lines.append(_table_header("rule", 12))
    for rule_id, counts in report.by_rule.items():
        lines.append(
            f"{rule_id:<12} {counts['tp']:>3} {counts['fn']:>3} {counts['fp']:>3}"
            f" {counts['tn']:>3}  {_fmt_rate_cell(counts['detection_rate']):>9}"
            f"  {_fmt_rate_cell(counts['fp_rate']):>7}"
        )
    lines.append("")
    by_rule = report.by_rule
    false_positives = sorted(
        entry_id for counts in by_rule.values() for entry_id in counts["false_positive_entries"]
    )
    expected_misses = sorted(
        entry_id for counts in by_rule.values() for entry_id in counts["expected_misses"]
    )
    unexpected = sorted(
        f"{entry_id} matched {rule_id}"
        for rule_id, counts in by_rule.items()
        for entry_id in counts["unexpected_matches"]
    )
    lines.append(_listings_line("false positive entries:", false_positives))
    lines.append(_listings_line("expected rule misses:", expected_misses))
    lines.append(_listings_line("unexpected matches:", unexpected))
    lines.append(_listings_line("skipped (excluded):", list(report.skipped)))
    lines.append(
        _listings_line(
            "rule errors:",
            [f"{err['rule_id']}: {err['error']}" for err in report.rule_errors],
        )
    )
    lines.append(
        _listings_line(
            "invalid lines:",
            [f"{line.file}:{line.line}: {line.reason}" for line in report.invalid_lines],
        )
    )
    if report.violations:
        lines.append("")
        for violation in report.violations:
            lines.append(f"THRESHOLD VIOLATION: {violation}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llmgw-replay",
        description="Measure detection rules against a labeled JSONL corpus.",
    )
    parser.add_argument(
        "--corpus", nargs="+", required=True, help="corpus .jsonl files or directories"
    )
    parser.add_argument("--rules", required=True, help="rules directory (*.jsonl)")
    parser.add_argument(
        "--canaries",
        default="",
        help="comma-separated canary values (input for the canary heuristic)",
    )
    parser.add_argument("--min-detection-rate", type=float, default=None, dest="min_detection_rate")
    parser.add_argument("--max-fp-rate", type=float, default=None, dest="max_fp_rate")
    parser.add_argument("--report", default=None, help="write the JSON report to this file")
    parser.add_argument(
        "--json", action="store_true", help="print the JSON report instead of the table"
    )
    parser.add_argument(
        "--live", metavar="BASE_URL", default=None, help="base URL of a running gateway"
    )
    parser.add_argument("--token", default=None, help="client token for live mode")
    parser.add_argument("--model", default=None, help="model name for live mode requests")
    parser.add_argument(
        "--events", default=None, help="gateway events.jsonl path for live-mode correlation"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.live:
        missing = [
            flag
            for flag, value in (
                ("--token", args.token),
                ("--model", args.model),
                ("--events", args.events),
            )
            if value is None
        ]
        if missing:
            print(f"error: live mode requires {' '.join(missing)}", file=sys.stderr)
            return 2
    canaries = [token for token in args.canaries.split(",") if token]
    try:
        report = run_replay(
            args.corpus,
            args.rules,
            canaries=canaries,
            min_detection_rate=args.min_detection_rate,
            max_fp_rate=args.max_fp_rate,
            live_url=args.live,
            token=args.token,
            model=args.model,
            events_path=args.events,
        )
    except (RulesetLoadError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_table(report))
    if args.report:
        Path(args.report).write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"json report: {args.report}", file=sys.stderr)
    return 1 if report.violations else 0
