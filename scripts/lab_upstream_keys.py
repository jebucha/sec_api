# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "openai>=1.54.0",
#     "anthropic>=0.40.0",
# ]
# ///
"""Lab acceptance 13.3: upstream credentials, and no client tokens upstream.

Sends one benign request through the gateway to each of two models (one
routed to a key-required upstream, one to a keyless upstream), then inspects
an upstream-side capture of the received request headers:

  1. the key-required upstream received ``Authorization: Bearer <key>``
     (exact match when ``--upstream-key`` is given),
  2. the keyless upstream received no ``Authorization`` and no ``x-api-key``
     header,
  3. the client's gateway token appears in no header sent to either upstream.

The capture is a JSONL file with one line per request —
``{"path", "model", "stream", "headers"}`` — produced by
``scripts/fake_upstream.py --header-log FILE``. Against real upstreams, put a
capture between the gateway and the upstream (e.g. ``mitmproxy``) and export
the flows in the same shape, or check the upstream's own access logs.

Run:

    uv run python scripts/fake_upstream.py --port 9101 --name gpu-box \
        --require-key "$VLLM_KEY" --header-log /tmp/up-headers.jsonl &
    uv run scripts/lab_upstream_keys.py --token "$LLMGW_TOKEN" \
        --upstream-key "$VLLM_KEY" --header-log /tmp/up-headers.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from anthropic import Anthropic
from openai import OpenAI

BENIGN = "What is the capital of France?"

results: list[tuple[str, str, str]] = []  # (status, check, detail)


def check(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"{status:13s} {name}" + (f" — {detail}" if detail else ""))


def read_new_lines(path: str, before_lines: int) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        lines = handle.readlines()
    return [json.loads(line) for line in lines[before_lines:] if line.strip()]


def count_lines(path: str) -> int:
    with open(path, encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gateway", default="http://127.0.0.1:8080", help="gateway base URL")
    parser.add_argument("--token", default=os.environ.get("LLMGW_TOKEN"),
                        help="client token (or LLMGW_TOKEN)")
    parser.add_argument("--model-keyed", default="qwen3-32b",
                        help="model routed to the key-required upstream (OpenAI format)")
    parser.add_argument("--model-keyless", default="llama-3.1-8b",
                        help="model routed to the keyless upstream (Anthropic format)")
    parser.add_argument("--upstream-key", default=None,
                        help="expected upstream credential; requires an exact Bearer match")
    parser.add_argument("--header-log", default=None,
                        help="upstream header capture (JSONL from fake_upstream.py --header-log)")
    args = parser.parse_args(argv)

    if not args.token:
        parser.error("client token required: --token or LLMGW_TOKEN")
    if args.header_log and not Path(args.header_log).is_file():
        parser.error(f"header log not found: {args.header_log}")

    oai = OpenAI(base_url=f"{args.gateway}/v1", api_key=args.token, timeout=120)
    anth = Anthropic(base_url=args.gateway, api_key=args.token, timeout=120)

    before = count_lines(args.header_log) if args.header_log else 0

    # --- one benign request per upstream --------------------------------------
    benign_msgs = [{"role": "user", "content": BENIGN}]
    try:
        oai.chat.completions.create(model=args.model_keyed, messages=benign_msgs)
        check("PASS", "traffic/keyed", f"request to {args.model_keyed} passed through")
    except Exception as exc:  # noqa: BLE001
        check("FAIL", "traffic/keyed", f"request to {args.model_keyed} failed: {exc!r}")
    try:
        anth.messages.create(model=args.model_keyless, max_tokens=2048, messages=benign_msgs)
        check("PASS", "traffic/keyless", f"request to {args.model_keyless} passed through")
    except Exception as exc:  # noqa: BLE001
        check("FAIL", "traffic/keyless", f"request to {args.model_keyless} failed: {exc!r}")

    if not args.header_log:
        check(
            "WARN", "capture",
            "no --header-log given: verify manually in the upstream access logs or a capture that "
            f"{args.model_keyed}'s upstream saw 'Authorization: Bearer <key>', "
            f"{args.model_keyless}'s upstream saw no auth header, "
            "and the client token is absent from both",
        )
        failed = [item for item in results if item[0] == "FAIL"]
        print(f"\n{'FAIL' if failed else 'OK (manual checks pending)'}: {len(failed)} failed")
        return 1 if failed else 0

    entries = read_new_lines(args.header_log, before)
    keyed = [entry for entry in entries if entry.get("model") == args.model_keyed]
    keyless = [entry for entry in entries if entry.get("model") == args.model_keyless]

    if len(keyed) != 1 or len(keyless) != 1:
        check("FAIL", "capture",
              f"expected one capture line per model, got keyed={len(keyed)} keyless={len(keyless)}")
        failed = [item for item in results if item[0] == "FAIL"]
        print(f"\nFAIL: {len(failed)} failed")
        return 1

    keyed_headers = keyed[0].get("headers", {})
    keyless_headers = keyless[0].get("headers", {})

    # 1. key-required upstream received the Bearer credential
    auth = keyed_headers.get("authorization")
    if args.upstream_key:
        if auth == f"Bearer {args.upstream_key}":
            check("PASS", "upstream/keyed-receives-key", "exact Bearer key match")
        else:
            check("FAIL", "upstream/keyed-receives-key",
                  f"authorization is {auth!r}, expected 'Bearer <upstream key>'")
    else:
        if auth and auth.startswith("Bearer "):
            check("PASS", "upstream/keyed-receives-key", "Authorization: Bearer present")
        else:
            check("FAIL", "upstream/keyed-receives-key",
                  f"authorization is {auth!r}, expected 'Bearer <key>'")

    # 2. keyless upstream received no auth headers at all
    stray = {name: value for name, value in keyless_headers.items()
             if name in ("authorization", "x-api-key")}
    if stray:
        check("FAIL", "upstream/keyless-no-auth", f"unexpected auth headers: {sorted(stray)}")
    else:
        check("PASS", "upstream/keyless-no-auth", "no Authorization or x-api-key header")

    # 3. the client token never reaches either upstream
    leaked = []
    for label, headers in (("keyed", keyed_headers), ("keyless", keyless_headers)):
        for name, value in headers.items():
            if args.token in value:
                leaked.append(f"{label}: {name}")
    if leaked:
        check("FAIL", "upstream/client-token-never-forwarded", f"client token found in: {leaked}")
    else:
        check("PASS", "upstream/client-token-never-forwarded",
              "client token absent from both upstreams")

    failed = [item for item in results if item[0] == "FAIL"]
    print(f"\n{'FAIL' if failed else 'OK'}: {len(failed)} failed, "
          f"{sum(1 for item in results if item[0] == 'PASS')} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
