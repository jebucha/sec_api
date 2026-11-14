"""5.7: every example rule in docs/rules.md must load via the real loader."""

from __future__ import annotations

import json
import re
from pathlib import Path

from llmgw.rules.loader import load_rules

DOCS = Path(__file__).parent.parent / "docs" / "rules.md"
DOC_CANARY = "DOCS-EXAMPLE-CANARY"


def _example_rules() -> list[dict]:
    text = DOCS.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", text, re.DOTALL)
    rules = []
    for block in blocks:
        for line in block.strip().splitlines():
            rule = json.loads(line)
            rules.append(rule)
    return rules


def test_every_doc_example_is_valid_json():
    rules = _example_rules()
    assert len(rules) >= 7
    ids = [rule["id"] for rule in rules]
    assert len(ids) == len(set(ids)), "example ids must be unique"


def test_every_doc_example_loads_with_zero_rejections(tmp_path):
    rules = _example_rules()
    lines = [json.dumps(rule) for rule in rules]
    (tmp_path / "doc-examples.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    events: list[dict] = []
    result = load_rules(tmp_path, event_sink=events.append, canary_values=[DOC_CANARY])
    assert result.rejected == 0, [e["reason"] for e in events]
    assert result.accepted == len(rules)
    # The disabled example loads but is not active
    assert "OFF-001" not in {r.id for r in result.ruleset.rules}
    # The canary example actually matches the documented test canary
    canary_rule = next(r for r in result.ruleset.rules if r.id == "PLK-001")
    assert canary_rule.matcher(f"here is {DOC_CANARY} for you")
