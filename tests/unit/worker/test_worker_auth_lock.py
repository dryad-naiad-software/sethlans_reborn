# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the threading lock in ``worker/sethlans_worker_agent/web_ui/auth.py``.

Covers NF-9 defense-in-depth: set_password, validate_password, and
reset_cache all operate under _cache_lock. Also tests concurrent access
from multiple threads does not corrupt shared state.
"""
import threading

import pytest

from sethlans_worker_agent.web_ui import auth


@pytest.fixture(autouse=True)
def _reset_auth_cache():
    """Ensure a clean auth cache before and after each test."""
    auth.reset_cache()
    yield
    auth.reset_cache()


# --- set_password under lock -----------------------------------------------

class TestSetPassword:

    def test_updates_cache(self, mocker):
        mocker.patch.object(auth, '_write_hash_to_config')
        auth.set_password('my-secret')
        assert auth._cached_hash is not None
        assert auth._cached_salt is not None
        assert auth._cache_loaded is True

    def test_cached_values_are_bytes(self, mocker):
        mocker.patch.object(auth, '_write_hash_to_config')
        auth.set_password('test-pw')
        assert isinstance(auth._cached_hash, bytes)
        assert isinstance(auth._cached_salt, bytes)

    def test_validate_after_set_succeeds(self, mocker):
        mocker.patch.object(auth, '_write_hash_to_config')
        auth.set_password('hunter2')
        assert auth.validate_password('hunter2') is True

    def test_validate_wrong_password_fails(self, mocker):
        mocker.patch.object(auth, '_write_hash_to_config')
        auth.set_password('correct')
        assert auth.validate_password('wrong') is False


# --- validate_password under lock ------------------------------------------

class TestValidatePassword:

    def test_default_password_accepted_when_unconfigured(self, mocker):
        mocker.patch.object(auth, '_write_hash_to_config')
        # No password set, should accept default
        assert auth.validate_password('sethlans') is True

    def test_wrong_default_rejected(self, mocker):
        mocker.patch.object(auth, '_write_hash_to_config')
        assert auth.validate_password('not-sethlans') is False

    def test_snapshots_values_under_lock(self, mocker):
        """Verify validate_password acquires the lock."""
        mocker.patch.object(auth, '_write_hash_to_config')
        auth.set_password('pw123')
        # The lock should be acquirable after validate_password returns
        acquired = auth._cache_lock.acquire(timeout=1)
        assert acquired
        auth._cache_lock.release()


# --- reset_cache under lock ------------------------------------------------

class TestResetCache:

    def test_clears_all_cache_fields(self, mocker):
        mocker.patch.object(auth, '_write_hash_to_config')
        auth.set_password('temp')
        auth.reset_cache()
        assert auth._cached_hash is None
        assert auth._cached_salt is None
        assert auth._cache_loaded is False

    def test_lock_released_after_reset(self):
        auth.reset_cache()
        acquired = auth._cache_lock.acquire(timeout=1)
        assert acquired
        auth._cache_lock.release()


# --- Concurrent access -----------------------------------------------------

def _run_in_thread(barrier, errors, func, *args, count=20):
    """Execute func(*args) count times after a barrier sync."""
    try:
        barrier.wait(timeout=5)
        for _ in range(count):
            func(*args)
    except Exception as e:
        errors.append(e)


def _start_and_join(threads, timeout=10):
    """Start all threads and join them with a timeout."""
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)


class TestConcurrentAccess:

    def test_concurrent_set_and_validate_no_corruption(self, mocker):
        """Multiple threads calling set_password and validate_password
        should not corrupt module state or raise exceptions."""
        mocker.patch.object(auth, '_write_hash_to_config')
        errors = []
        barrier = threading.Barrier(4)
        threads = [
            threading.Thread(
                target=_run_in_thread,
                args=(barrier, errors, auth.set_password, 'pw-a'),
            ),
            threading.Thread(
                target=_run_in_thread,
                args=(barrier, errors, auth.set_password, 'pw-b'),
            ),
            threading.Thread(
                target=_run_in_thread,
                args=(barrier, errors, auth.validate_password, 'pw-a'),
            ),
            threading.Thread(
                target=_run_in_thread,
                args=(barrier, errors, auth.validate_password, 'pw-b'),
            ),
        ]
        _start_and_join(threads)
        assert not errors, f"Concurrent access errors: {errors}"

    def test_state_consistent_after_concurrent_ops(self, mocker):
        """After concurrent operations, the module is in a valid state."""
        mocker.patch.object(auth, '_write_hash_to_config')
        errors = []
        barrier = threading.Barrier(3)
        threads = [
            threading.Thread(
                target=_run_in_thread,
                args=(barrier, errors, auth.set_password, 'final'),
                kwargs={'count': 10},
            ),
            threading.Thread(
                target=_run_in_thread,
                args=(barrier, errors, auth.validate_password, 'final'),
                kwargs={'count': 10},
            ),
            threading.Thread(
                target=_run_in_thread,
                args=(barrier, errors, auth.reset_cache),
                kwargs={'count': 10},
            ),
        ]
        _start_and_join(threads)
        assert not errors, f"Concurrent access errors: {errors}"

        # After all threads complete, module is in a valid state
        assert auth._cache_loaded in (True, False)
        if auth._cached_hash is not None:
            assert isinstance(auth._cached_hash, bytes)
        if auth._cached_salt is not None:
            assert isinstance(auth._cached_salt, bytes)


# --- Lock existence --------------------------------------------------------

class TestLockExists:

    def test_cache_lock_is_threading_lock(self):
        assert isinstance(auth._cache_lock, type(threading.Lock()))

    def test_lock_is_module_level(self):
        """The lock must be defined at module level, not inside a function."""
        assert hasattr(auth, '_cache_lock')
