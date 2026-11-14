# Starter ruleset baseline

The baseline below is the replay of the starter corpus (`corpus/`) against the
starter rule packs (`rules/`) with the documented test-only canary supplied
via `--canaries` (design D11). The command and its output are the reference
point for rule changes: run it after editing any rule and compare against the
numbers here.

## Reproducing the baseline

```console
$ uv run llmgw-replay --corpus corpus/ --rules rules/ --canaries LLMGW-TEST-CANARY-0000
```

The command must be run from the repository root (the paths are relative).
`--canaries` is required for the canary prompt-leak rule (PL-001) to load:
canary values never live in the rule pack.

## Baseline report

Captured from the command above (the ruleset hash is a content hash over the
active rules; it changes whenever any rule changes).

```
llmgw-replay (offline)
ruleset: 224951a6d389  active rules: 16  canaries: LLMGW-TEST-CANARY-0000
entries: 29  evaluated: 29  invalid lines: 0

overall       tp=17  fn=0  fp=0  tn=12  detection=1.0  fp_rate=0.0

by category:
category              tp  fn  fp  tn  detection  fp_rate
jailbreak              3   0   0   2        1.0      0.0
pii                    2   0   0   2        1.0      0.0
prompt-injection       3   0   0   2        1.0      0.0
prompt-leak            2   0   0   2        1.0      0.0
secrets                4   0   0   2        1.0      0.0
smuggling              3   0   0   2        1.0      0.0

by rule:
rule          tp  fn  fp  tn  detection  fp_rate
JB-001         1   0   0  12        1.0      0.0
JB-002         1   0   0  12        1.0      0.0
JB-003         1   0   0  12        1.0      0.0
PI-001         1   0   0  12        1.0      0.0
PI-002         1   0   0  12        1.0      0.0
PI-003         1   0   0  12        1.0      0.0
PII-001        1   0   0  12        1.0      0.0
PII-002        1   0   0  12        1.0      0.0
PL-001         2   0   0  12        1.0      0.0
SE-001         1   0   0  12        1.0      0.0
SE-002         1   0   0  12        1.0      0.0
SE-003         1   0   0  12        1.0      0.0
SE-004         1   0   0  12        1.0      0.0
SM-001         1   0   0  12        1.0      0.0
SM-002         1   0   0  12        1.0      0.0
SM-003         1   0   0  12        1.0      0.0

false positive entries: (none)
expected rule misses:   (none)
unexpected matches:     (none)
skipped (excluded):     (none)
rule errors:            (none)
invalid lines:          (none)
```

## Notes

- Every starter pack category has at least one true positive and at least one
  evaluated benign entry; every rule has at least one declared positive.
- All benign corpus entries are near-misses designed to probe false
  positives; the baseline has zero false positives.
- The corpus contains 17 malicious and 12 benign entries across the six
  categories, in both wire formats, including response-stage prompt-leak
  entries that use the test-only canary `LLMGW-TEST-CANARY-0000`.
- CI thresholds can be pinned with `--min-detection-rate` and
  `--max-fp-rate`; the baseline passes e.g.
  `--min-detection-rate 1.0 --max-fp-rate 0.0`.
