"""``llmgw-token``: generate a client token and its sha256 for the config."""

from __future__ import annotations

import hashlib
import secrets
import sys

_PREFIX = "llmgw_"


def generate_token() -> str:
    """A fresh random token (256 bits of entropy, urlsafe)."""
    return _PREFIX + secrets.token_urlsafe(32)


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    name = args[0] if args else "client"
    if any(not arg or arg.startswith("-") for arg in args):
        print("usage: llmgw-token <client-name>", file=sys.stderr)
        return 2
    token = generate_token()
    print(f"client: {name}")
    print(f"token: {token}")
    print(f"token_sha256: {token_sha256(token)}")
    print("# Store the sha256 in gateway.yaml: clients." + name + ".token_sha256")
    return 0
