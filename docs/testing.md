# Testing detection rules: corpus and replay tool

Detection content is versioned like code: every rule change is measured
against the labeled corpus in `corpus/` with the `llmgw-replay` tool, before
and after. The reference numbers live in [baseline.md](baseline.md).

## Corpus format

Corpus files are JSONL; one labeled example per line. Blank lines and lines
beginning with `//` are ignored.

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `id` | yes | string | Unique across the whole corpus run. |
| `text` | yes | string | The content to evaluate. |
| `label` | yes | `malicious` \| `benign` | Ground truth. |
| `category` | yes | string | The starter pack category, e.g. `prompt-injection`. |
| `expected_rules` | no | list of rule ids | Rules expected to match this entry (per-rule true positives/false negatives). |
| `field` | no | `system` \| `user_text` \| `assistant_text` \| `reasoning_text` | Canonical field the text is placed in. Default `user_text`. |
| `stage` | no | `request` \| `response` | Stage the entry is evaluated at. Default `request`. |
| `format` | no | `openai` \| `anthropic` | Wire format for live mode. Default `openai`; ignored offline. |

A response-stage entry places `text` in the response canonical structure, so
response rules such as `prompt-leak` can be exercised offline. In live mode
response-stage entries are reported as **skipped** (they cannot be sent as
requests).

Invalid lines (bad JSON, missing/invalid fields, unknown fields, duplicate
ids) are reported in the report and excluded from the metrics; they never
abort the run. Skipped entries (live mode) are likewise reported and excluded
from every metric, so a response-stage entry is never counted as TN or FN.

## The test-only canary

Prompt-leak entries use the documented test-only canary
**`LLMGW-TEST-CANARY-0000`**. It is never used in a real deployment: real
canary values come from the environment variable named by `canary_tokens_env`
(design D11). Supply the test canary with `--canaries` so the canary rule
(PL-001) loads and matches the corpus entries.

## Usage

From the repository root:

```console
# Offline replay of the starter corpus against the starter packs.
# No gateway, no upstream, no network.
$ uv run llmgw-replay --corpus corpus/ --rules rules/ --canaries LLMGW-TEST-CANARY-0000
```

The human-readable table goes to stdout. For a machine-readable report:

```console
# Write the JSON report to a file (table still on stdout).
$ uv run llmgw-replay --corpus corpus/ --rules rules/ --canaries LLMGW-TEST-CANARY-0000 --report report.json

# Print the JSON report instead of the table.
$ uv run llmgw-replay --corpus corpus/ --rules rules/ --canaries LLMGW-TEST-CANARY-0000 --json
```

CI-friendly thresholds — the tool exits non-zero when either is violated
(null rates, e.g. an empty corpus class, count as violations):

```console
$ uv run llmgw-replay --corpus corpus/ --rules rules/ --canaries LLMGW-TEST-CANARY-0000 \
    --min-detection-rate 1.0 --max-fp-rate 0.0
```

Live replay sends request-stage entries through a running gateway as real
requests in each entry's `format`, using the client token, and correlates each
response with the events file via the `X-Gateway-Request-Id` response header
(which equals the `request_id` of every event for that request). Response-stage
entries are reported as skipped:

```console
$ uv run llmgw-replay --corpus corpus/ --rules rules/ --canaries LLMGW-TEST-CANARY-0000 \
    --live http://127.0.0.1:8080 --token <client-token> --model qwen3-32b \
    --events /path/to/events.jsonl
```

`--token` is a client token (see `uv run llmgw-token`); `--events` is the
gateway's events file (the `events.path` from its `gateway.yaml`).

## Metrics

Metrics are computed over evaluated entries only (invalid lines and live-mode
skipped entries are excluded).

- *Detected* = at least one rule matched the entry, whatever the rule's
  action.
- **Overall / per category**: TP = malicious and detected, FN = malicious and
  not detected, FP = benign and detected, TN = benign and not detected.
  Detection rate = TP/(TP+FN); false-positive rate = FP/(FP+TN). A rate whose
  denominator is 0 is reported as `null`.
- **Per rule R**: positives are the malicious entries whose `expected_rules`
  contains R; negatives are all benign entries. TP/FP/FN/TN follow the same
  pattern. Malicious entries that do not list R are excluded from R's counts;
  a match of R on one of them is listed as an *unexpected match*.

The report lists, with entry ids: false positives (benign entries that
matched), expected-rule misses (malicious entries their declared rule missed),
unexpected matches, skipped entries (live mode), rule errors, and invalid
corpus lines.
