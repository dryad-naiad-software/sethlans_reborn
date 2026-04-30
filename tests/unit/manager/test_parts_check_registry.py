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
from concurrent.futures import ThreadPoolExecutor

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


# ---- 50-thread concurrent stress test (spec Tests §415, AC §486) ----
#
# Regression guard against a future change that accidentally widens
# the registry mutation lock to span I/O or the check_fn body.  The
# 50 ms ceiling is NOT a performance SLO; it is a lock-scope-creep
# tripwire.  If `_status_lock` ever ended up held across the check
# body, a reader would block for the full check_fn duration (200 ms
# here) and this test would catch it.

@pytest.mark.timeout(5)
def test_50_thread_get_status_concurrent_stress_no_torn_reads():
    """50 readers vs in-flight check: no torn reads, no >50 ms blocks.

    The check_fn sleeps 200 ms while 50 reader threads pound on
    ``get_status``.  Each reader records its wall-clock latency.
    The lock is held only across the brief reference swap, so every
    reader call must complete quickly even though the check_fn is
    busy.
    """
    check_started = threading.Event()
    release_check = threading.Event()

    def slow_check():
        check_started.set()
        # Simulate I/O / subprocess work inside the check.
        if not release_check.wait(timeout=2.0):
            raise RuntimeError("test fixture release_check never fired")
        time.sleep(0.05)
        return registry.Status(
            status="ready", source="system", version="8", path="/x",
        )

    registry.register_part("ffmpeg", slow_check)
    registry.run_parts_check()

    assert check_started.wait(timeout=2.0), "check_fn never started"

    latencies: list[float] = []
    snapshots: list[registry.Status] = []
    errors: list[BaseException] = []

    def reader():
        try:
            t0 = time.monotonic()
            snapshot = registry.get_status("ffmpeg")
            elapsed = time.monotonic() - t0
            latencies.append(elapsed)
            snapshots.append(snapshot)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    # Hammer get_status from 50 threads while the check_fn is mid-sleep.
    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = [pool.submit(reader) for _ in range(50)]
        for fut in futures:
            fut.result(timeout=4.0)

    # Allow the check_fn to finish so the daemon thread cleans up.
    release_check.set()
    for _ in range(100):
        if registry.get_status("ffmpeg").status == "ready":
            break
        time.sleep(0.01)

    # 1. No exceptions raised by any reader.
    assert errors == [], f"reader errors: {errors!r}"

    # 2. Every snapshot is a well-formed frozen Status (no torn reads).
    assert len(snapshots) == 50
    for snap in snapshots:
        assert isinstance(snap, registry.Status)
        assert dataclasses.is_dataclass(snap)
        # Frozen dataclass: mutation must raise.
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.status = "tampered"  # type: ignore[misc]
        # Status field is one of the closed-vocabulary states.
        assert snap.status in ("installing", "ready", "failed")

    # 3. Lock-scope guard: no reader blocks longer than 50 ms.
    max_latency = max(latencies)
    assert max_latency < 0.050, (
        f"a reader blocked {max_latency * 1000:.1f}ms — registry lock "
        f"may be held across the check_fn body"
    )
