"""Tests for the llmgw-token entry point (task 3.3)."""

from __future__ import annotations

import re

from helpers import make_test_config
from llmgw.auth import authenticate, sha256_hex
from llmgw.config import Client
from llmgw.token import generate_token, main, token_sha256


def test_llmgw_token_outputs_token_and_sha256(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    token_match = re.search(r"^token: (\S+)$", out, re.MULTILINE)
    hash_match = re.search(r"^token_sha256: ([0-9a-f]{64})$", out, re.MULTILINE)
    assert token_match and hash_match
    token = token_match.group(1)
    assert hash_match.group(1) == token_sha256(token)
    assert "client: demo" in out


def test_generated_token_authenticates_with_its_hash(tmp_path):
    token = generate_token()
    digest = token_sha256(token)
    config = make_test_config(
        tmp_path,
        clients={"demo": Client(name="demo", token_sha256=digest)},
    )
    assert authenticate(config, token) is not None
    assert authenticate(config, token).name == "demo"
    assert authenticate(config, token + "x") is None
    assert sha256_hex(token) == digest


def test_llmgw_token_rejects_flags(capsys):
    assert main(["--help"]) == 2
    assert "usage" in capsys.readouterr().err
