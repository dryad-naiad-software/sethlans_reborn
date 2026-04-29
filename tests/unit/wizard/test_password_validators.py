# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Smoke tests for ``wizard/sethlans_wizard/password_validators.py``
(FR-M2-5)."""

from __future__ import annotations

import pytest

from wizard.sethlans_wizard import password_validators as pv


@pytest.fixture(autouse=True)
def _reset_resource_cache():
    pv.reset_resource_cache_for_tests()
    yield
    pv.reset_resource_cache_for_tests()


def test_resource_loads_and_verifies():
    """The shipped resource matches the pinned SHA-256."""
    assert pv.verify_resource() is None


def test_strong_password_passes():
    failures = pv.validate_password(
        "Tr0ub4dor&3xp1l@in", user_attrs=["alice", "alice@example.org"],
    )
    assert failures == []


def test_short_password_rejected():
    failures = pv.validate_password("hi", user_attrs=[])
    assert "password_too_short" in failures


def test_oversized_password_rejected_immediately():
    failures = pv.validate_password("a" * 5000, user_attrs=[])
    assert failures == ["password_too_long"]


def test_common_password_rejected():
    failures = pv.validate_password("password", user_attrs=[])
    assert "password_too_common" in failures


def test_all_numeric_rejected():
    failures = pv.validate_password("12345678901", user_attrs=[])
    # all-numeric AND would be flagged if in common list; assert numeric.
    assert "password_entirely_numeric" in failures


def test_user_attribute_similarity_rejected():
    failures = pv.validate_password(
        "alicesPassword!1A", user_attrs=["alicesPassword!1A"],
    )
    assert "password_too_similar" in failures
