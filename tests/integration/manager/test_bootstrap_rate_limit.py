# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Bootstrap rate-limit integration (FR-2).

 * Any 10 attempts per IP per 5min (success or failure).
 * 11th attempt → 429 rate_limited with empty details.
 * Different IP resets the counter.
"""

from __future__ import annotations

import pytest
from django.test import Client

from tests.integration.manager._setup_helpers import (
    VALID_TOKEN,
    bootstrap,
    enter_setup_mode,
    exit_setup_mode,
    patch_data_dir,
    reset_rate_limiter,
)


def _bootstrap_from(ip: str, token: str = VALID_TOKEN):
    client = Client(REMOTE_ADDR=ip)
    return bootstrap(client, token=token), client


@pytest.fixture
def setup_env(mocker, tmp_path):
    enter_setup_mode(mocker)
    reset_rate_limiter(mocker)
    data_dir = patch_data_dir(mocker, tmp_path)
    yield data_dir
    exit_setup_mode()


@pytest.mark.django_db
class TestBootstrapRateLimit:

    def test_ten_attempts_permitted(self, setup_env):
        for _ in range(10):
            resp, _ = _bootstrap_from("10.0.0.5", token="wrong")
            assert resp.status_code != 429

    def test_eleventh_attempt_rate_limited(self, setup_env):
        for _ in range(10):
            _bootstrap_from("10.0.0.6", token="wrong")
        resp, _ = _bootstrap_from("10.0.0.6", token="wrong")
        assert resp.status_code == 429
        body = resp.json()
        assert body["error"]["code"] == "rate_limited"
        assert body["error"]["details"] == {}
        # Spec: Retry-After (if present) is a fixed 300, never remaining.
        retry = resp.get("Retry-After")
        if retry is not None:
            assert retry == "300"

    def test_different_ip_not_affected(self, setup_env):
        for _ in range(10):
            _bootstrap_from("10.0.0.7", token="wrong")
        resp, _ = _bootstrap_from("10.0.0.8", token="wrong")
        assert resp.status_code != 429

    def test_counts_successful_attempts_too(self, setup_env):
        # The limiter counts attempts regardless of outcome (FR-2 note
        # "counts attempts regardless of outcome").
        for _ in range(10):
            resp, _ = _bootstrap_from("10.0.0.9", token=VALID_TOKEN)
            # Success (204) or 429 — but 204 for the first 10.
            assert resp.status_code in (204, 429)
        resp, _ = _bootstrap_from("10.0.0.9", token=VALID_TOKEN)
        assert resp.status_code == 429
