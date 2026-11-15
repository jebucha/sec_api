# client-auth Specification

## Purpose

Authenticates lab clients to the gateway with per-client tokens so that every request is attributable to a named client identity, independent of any upstream credentials.

## Requirements

### Requirement: Token required on all API endpoints
The gateway SHALL require a valid client token on every API endpoint and reject requests without one before any parsing, detection, or upstream contact.

#### Scenario: Missing token
- **WHEN** a client calls any API endpoint without a token
- **THEN** the gateway responds 401 with a provider-shaped authentication error and contacts no upstream
- **ID**: T-CLIENT-AUTH-001
- **File**: tests/test_auth.py::test_missing_token_returns_401_without_upstream_contact
- **Command**: `uv run pytest tests/test_auth.py::test_missing_token_returns_401_without_upstream_contact`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Invalid token
- **WHEN** a client presents a token that matches no configured client
- **THEN** the gateway responds 401 and records an authentication-failure event that does not include the presented token
- **ID**: T-CLIENT-AUTH-002
- **File**: tests/test_auth.py::test_invalid_token_returns_401_and_event_omits_token
- **Command**: `uv run pytest tests/test_auth.py::test_invalid_token_returns_401_and_event_omits_token`
- **Pass**: exit code 0, no skipped tests

### Requirement: Both SDK header styles accepted
The gateway SHALL accept the client token from either `Authorization: Bearer <token>` or `x-api-key: <token>`.

#### Scenario: OpenAI SDK style
- **WHEN** a client sends `Authorization: Bearer <valid token>`
- **THEN** the request is authenticated
- **ID**: T-CLIENT-AUTH-003
- **File**: tests/test_auth.py::test_bearer_header_authenticates
- **Command**: `uv run pytest tests/test_auth.py::test_bearer_header_authenticates`
- **Pass**: exit code 0, no skipped tests

#### Scenario: Anthropic SDK style
- **WHEN** a client sends `x-api-key: <valid token>`
- **THEN** the request is authenticated
- **ID**: T-CLIENT-AUTH-004
- **File**: tests/test_auth.py::test_x_api_key_header_authenticates
- **Command**: `uv run pytest tests/test_auth.py::test_x_api_key_header_authenticates`
- **Pass**: exit code 0, no skipped tests

### Requirement: Hashed token storage
Client tokens SHALL be configured as SHA-256 hex digests, never as plaintext. The gateway SHALL compare presented tokens against them in constant time.

#### Scenario: Config holds only hashes
- **WHEN** a client entry is configured
- **THEN** the configuration contains the token's SHA-256 digest and the gateway authenticates the matching plaintext token presented by the client
- **ID**: T-CLIENT-AUTH-005
- **File**: tests/test_auth.py::test_sha256_digest_in_config_authenticates_plaintext_token
- **Command**: `uv run pytest tests/test_auth.py::test_sha256_digest_in_config_authenticates_plaintext_token`
- **Pass**: exit code 0, no skipped tests

### Requirement: Client identity attribution
Each authenticated request SHALL be associated with the configured client name. That name SHALL appear in all security events for the request.

#### Scenario: Events name the client
- **GIVEN** a client entry named `opencode-laptop`
- **WHEN** that client's request produces a security event
- **THEN** the event's client field is `opencode-laptop`
- **ID**: T-CLIENT-AUTH-006
- **File**: tests/test_auth.py::test_events_carry_client_name
- **Command**: `uv run pytest tests/test_auth.py::test_events_carry_client_name`
- **Pass**: exit code 0, no skipped tests

### Requirement: Health endpoint without auth
The gateway SHALL serve an unauthenticated `GET /healthz` that reveals no configuration, model, or rule details.

#### Scenario: Health probe
- **WHEN** anything calls `GET /healthz`
- **THEN** the gateway responds 200 with the JSON body `{"status":"ok"}` and nothing else
- **ID**: T-CLIENT-AUTH-007
- **File**: tests/test_auth.py::test_healthz_unauthenticated_returns_status_ok_only
- **Command**: `uv run pytest tests/test_auth.py::test_healthz_unauthenticated_returns_status_ok_only`
- **Pass**: exit code 0, no skipped tests
