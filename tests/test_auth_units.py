"""Unit tests for the pure auth logic (client-auth spec).

The app-level scenarios (401 responses, events, /healthz) live in
tests/test_auth.py and run against the in-process app.
"""

from __future__ import annotations

from starlette.datastructures import Headers

from helpers import TEST_TOKEN, TEST_TOKEN_SHA256, make_test_config
from llmgw.auth import authenticate, extract_token, sha256_hex


def test_extract_bearer():
    token, style = extract_token(Headers({"Authorization": f"Bearer {TEST_TOKEN}"}))
    assert token == TEST_TOKEN
    assert style == "bearer"


def test_extract_bearer_case_insensitive_scheme():
    token, style = extract_token(Headers({"Authorization": f"bEARER {TEST_TOKEN}"}))
    assert token == TEST_TOKEN
    assert style == "bearer"


def test_extract_x_api_key():
    token, style = extract_token(Headers({"x-api-key": TEST_TOKEN}))
    assert token == TEST_TOKEN
    assert style == "x-api-key"


def test_extract_missing():
    assert extract_token(Headers({})) == (None, None)
    assert extract_token(Headers({"Authorization": "Basic abc"})) == (None, None)
    assert extract_token(Headers({"Authorization": "Bearer "})) == (None, None)


def test_sha256_digest_in_config_authenticates_plaintext_token(tmp_path):
    """T-CLIENT-AUTH-005: config holds only the digest; plaintext authenticates."""
    config = make_test_config(tmp_path)
    assert authenticate(config, TEST_TOKEN) is not None
    assert authenticate(config, TEST_TOKEN).name == "opencode-laptop"
    assert authenticate(config, "wrong-token") is None
    # The plaintext token appears nowhere in the config
    assert TEST_TOKEN not in repr(config.clients)
    assert sha256_hex(TEST_TOKEN) == TEST_TOKEN_SHA256
