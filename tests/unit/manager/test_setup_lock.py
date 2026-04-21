# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for :mod:`workers.services.setup_lock`.

Mirror of the worker's ``test_setup_lock.py`` — the manager lock
behaviour must match the spec's "two concurrent POSTs → exactly one
200, one 409" acceptance. GET endpoints must NOT take the lock
(tested in the integration suite; here we only verify the lock
itself is non-blocking and fail-fast).
"""

from __future__ import annotations

import threading
import time

import pytest

from workers.services.setup_lock import (
    is_setup_mutation_in_progress,
    setup_conflict_response,
    setup_mutation_lock,
)


def test_acquire_first_yields_true():
    with setup_mutation_lock() as acquired:
        assert acquired is True


def test_reentrant_same_thread_returns_false():
    """Non-reentrant by design: a nested call from the same thread
    gets False (surfaces a design smell as 409)."""
    with setup_mutation_lock() as outer_ok:
        assert outer_ok is True
        with setup_mutation_lock() as inner_ok:
            assert inner_ok is False


def test_released_after_context_exit():
    with setup_mutation_lock() as first_ok:
        assert first_ok is True
    # Acquired again fresh — lock was released.
    with setup_mutation_lock() as second_ok:
        assert second_ok is True


def test_released_even_when_body_raises():
    with pytest.raises(ValueError):
        with setup_mutation_lock() as acquired:
            assert acquired is True
            raise ValueError("handler exploded")
    # Lock must have been released despite the exception.
    with setup_mutation_lock() as after:
        assert after is True


def test_other_thread_holding_sees_false():
    """While thread A holds the lock, thread B must get False
    immediately (no blocking)."""
    barrier = threading.Event()
    release = threading.Event()
    b_result: dict = {}

    def holder():
        with setup_mutation_lock() as acquired:
            assert acquired is True
            barrier.set()
            # Hold until the probe thread has run.
            assert release.wait(timeout=2.0)

    def probe():
        assert barrier.wait(timeout=2.0)
        start = time.monotonic()
        with setup_mutation_lock() as acquired:
            b_result['acquired'] = acquired
        b_result['elapsed'] = time.monotonic() - start

    t_a = threading.Thread(target=holder)
    t_b = threading.Thread(target=probe)
    t_a.start()
    t_b.start()
    t_b.join(timeout=3.0)
    release.set()
    t_a.join(timeout=3.0)

    assert b_result['acquired'] is False
    # Fail-fast: non-blocking acquire must return quickly.
    assert b_result['elapsed'] < 0.1


def test_is_setup_mutation_in_progress_diagnostic():
    """Diagnostic helper reports lock state; may race but snapshots
    current state."""
    assert is_setup_mutation_in_progress() is False
    with setup_mutation_lock():
        assert is_setup_mutation_in_progress() is True
    assert is_setup_mutation_in_progress() is False


def test_setup_conflict_response_shape():
    """409 response carries the canonical error envelope."""
    resp = setup_conflict_response()
    assert resp.status_code == 409
    payload = resp.data
    assert "error" in payload
    assert payload["error"]["code"] == "setup_in_progress"
    assert "message" in payload["error"]
    assert "details" in payload["error"]
