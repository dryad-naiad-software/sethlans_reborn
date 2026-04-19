# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared fixtures for frontend-backend contract integration tests.

These tests verify that the live Django API responds with shapes the
Angular frontend expects -- specifically the envelopes and payloads
defined in:

* ``manager/frontend/src/app/core/models/error-envelope.ts``
* ``manager/frontend/src/app/features/setup/models/setup.models.ts``

Tests here do NOT mock the backend; they exercise the real Django
URLconf + middleware + DRF via the test client.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_ROOT = REPO_ROOT / "manager" / "frontend" / "src" / "app"
ERROR_ENVELOPE_TS = FRONTEND_ROOT / "core" / "models" / "error-envelope.ts"
SETUP_MODELS_TS = (
    FRONTEND_ROOT / "features" / "setup" / "models" / "setup.models.ts"
)


@pytest.fixture
def error_envelope_ts_source() -> str:
    """Return the contents of the TS SetupErrorEnvelope definition."""
    return ERROR_ENVELOPE_TS.read_text(encoding="utf-8")


@pytest.fixture
def setup_models_ts_source() -> str:
    """Return the contents of the TS setup models file."""
    return SETUP_MODELS_TS.read_text(encoding="utf-8")


@pytest.fixture
def enter_setup_mode(mocker):
    """Force the setup gate into setup-mode (sentinel absent)."""
    from sethlans_manager.middleware import setup_gate
    prev = setup_gate._setup_complete
    setup_gate._setup_complete = False
    mocker.patch.object(
        setup_gate, "_check_sentinel", return_value=False,
    )
    yield
    setup_gate._setup_complete = prev


@pytest.fixture
def exit_setup_mode(mocker):
    """Force the setup gate into post-setup mode (sentinel present)."""
    from sethlans_manager.middleware import setup_gate
    prev = setup_gate._setup_complete
    setup_gate._setup_complete = True
    mocker.patch.object(
        setup_gate, "_check_sentinel", return_value=True,
    )
    yield
    setup_gate._setup_complete = prev


@pytest.fixture
def fresh_bootstrap_limiter(mocker):
    """Install a fresh rate limiter on the bootstrap view."""
    from workers.rate_limiter import InMemoryRateLimiter
    from workers.views import setup_bootstrap as bootstrap_mod
    limiter = InMemoryRateLimiter(max_attempts=10, window_seconds=300)
    mocker.patch.object(
        bootstrap_mod, "_bootstrap_rate_limiter", limiter,
    )
    return limiter


VALID_TEST_TOKEN = "a" * 64  # >=32 bytes, matches bootstrap min-length


@pytest.fixture
def patch_setup_token(mocker):
    """Stub ``read_setup_token`` to return the canonical test token."""
    from workers.views import setup_bootstrap as bootstrap_mod
    mocker.patch.object(
        bootstrap_mod, "read_setup_token", return_value=VALID_TEST_TOKEN,
    )
    return VALID_TEST_TOKEN


@pytest.fixture
def patch_bootstrap_data_dir(mocker, tmp_path):
    """Route bootstrap data_dir and ini-bind to tmp_path."""
    from workers.views import setup_bootstrap as bootstrap_mod
    mocker.patch.object(bootstrap_mod, "_data_dir", return_value=tmp_path)
    mocker.patch.object(
        bootstrap_mod, "bind_setup_session_id", return_value=True,
    )
    return tmp_path


def assert_envelope_shape(body: dict) -> None:
    """Assert the body matches the unified ``SetupErrorEnvelope`` shape.

    Mirrors the TS interface:

        { error: { code: SetupErrorCode, message: string, details: object } }
    """
    assert isinstance(body, dict), f"Expected dict, got {type(body)}"
    assert "error" in body, f"No 'error' key in body: {body!r}"
    err = body["error"]
    assert isinstance(err, dict), f"'error' must be dict, got {type(err)}"
    assert set(err.keys()) >= {"code", "message", "details"}, (
        f"Envelope missing required keys: {err!r}"
    )
    assert isinstance(err["code"], str) and err["code"]
    assert isinstance(err["message"], str)
    assert isinstance(err["details"], dict)
