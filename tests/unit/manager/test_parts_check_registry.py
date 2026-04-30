# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Smoke tests for the parts-check registry primitives.

The full coverage matrix from spec Tests §415 (50-thread stress, etc.)
is owned by the test agents — these tests verify the wiring only.
"""

import dataclasses
import threading
import time

import pytest

from workers.services.parts_check import registry


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset registry state between tests."""
    registry._reset_for_tests()
    yield
    registry._reset_for_tests()


def test_status_is_frozen_dataclass():
    """Status objects must be frozen — mutation raises."""
    status = registry.Status(status="ready", source="system")
    assert dataclasses.is_dataclass(status)
    with pytest.raises(dataclasses.FrozenInstanceError):
        status.status = "failed"  # type: ignore[misc]


def test_get_status_unknown_part_returns_default():
    """Unknown part name returns Status(status='installing')."""
    snapshot = registry.get_status("nonexistent")
    assert snapshot.status == "installing"
    assert snapshot.error is None


def test_register_and_publish():
    """Registering a part and running checks publishes the status."""
    expected = registry.Status(
        status="ready", source="system", version="8", path="/usr/bin/x",
    )

    def check_fn():
        return expected

    registry.register_part("smoke", check_fn)
    registry.run_parts_check()
    # Wait briefly for the daemon thread; the check is in-memory.
    for _ in range(50):
        if registry.get_status("smoke").status == "ready":
            break
        time.sleep(0.02)
    assert registry.get_status("smoke") == expected


def test_run_parts_check_idempotent():
    """Multiple run_parts_check() calls spawn at most one thread."""
    started = []
    barrier = threading.Event()

    def slow_check():
        started.append(1)
        barrier.wait(timeout=2.0)
        return registry.Status(status="ready")

    registry.register_part("slow", slow_check)
    registry.run_parts_check()
    registry.run_parts_check()
    registry.run_parts_check()
    # Give the thread a moment to enter the check_fn.
    for _ in range(20):
        if started:
            break
        time.sleep(0.01)
    barrier.set()
    # Wait for finish.
    for _ in range(50):
        if registry.get_status("slow").status == "ready":
            break
        time.sleep(0.02)
    assert len(started) == 1


def test_check_fn_exception_publishes_failed():
    """A check_fn that raises results in status='failed'."""

    def buggy():
        raise RuntimeError("boom")

    registry.register_part("buggy", buggy)
    registry.run_parts_check()
    for _ in range(50):
        snapshot = registry.get_status("buggy")
        if snapshot.status == "failed":
            break
        time.sleep(0.02)
    assert registry.get_status("buggy").status == "failed"
    assert registry.get_status("buggy").error is not None
