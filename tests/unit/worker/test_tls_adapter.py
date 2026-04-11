# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the tls_adapter module.

Tests PinningHTTPAdapter fingerprint verification, thread-local session
management, and session reset for re-enrollment.
"""
import threading

import requests
import requests.adapters

from sethlans_worker_agent.tls_adapter import (
    PinningHTTPAdapter,
    _thread_local,
    get_session,
    reset_sessions,
)


# --- PinningHTTPAdapter ---

class TestPinningHTTPAdapter:

    def test_stores_expected_fingerprint(self):
        fp = 'a' * 64
        adapter = PinningHTTPAdapter(expected_fingerprint=fp)
        assert adapter.expected_fingerprint == fp

    def test_init_poolmanager_sets_assert_fingerprint(self, mocker):
        fp = 'b' * 64
        adapter = PinningHTTPAdapter(expected_fingerprint=fp)
        mock_super = mocker.patch.object(
            requests.adapters.HTTPAdapter,
            'init_poolmanager',
        )
        adapter.init_poolmanager(1, 2, block=False)
        mock_super.assert_called_once_with(
            1, 2, block=False, assert_fingerprint=fp
        )

    def test_proxy_manager_for_sets_assert_fingerprint(self, mocker):
        fp = 'c' * 64
        adapter = PinningHTTPAdapter(expected_fingerprint=fp)
        mock_super = mocker.patch.object(
            requests.adapters.HTTPAdapter,
            'proxy_manager_for',
        )
        adapter.proxy_manager_for('http://proxy:8080', some_kw='val')
        mock_super.assert_called_once_with(
            'http://proxy:8080',
            some_kw='val',
            assert_fingerprint=fp,
        )

    def test_inherits_from_http_adapter(self):
        adapter = PinningHTTPAdapter(expected_fingerprint='x' * 64)
        assert isinstance(adapter, requests.adapters.HTTPAdapter)


# --- get_session ---

class TestGetSession:

    def setup_method(self):
        """Ensure clean thread-local state before each test."""
        if hasattr(_thread_local, 'session'):
            del _thread_local.session

    def test_returns_requests_session(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.tls_adapter.config.CERT_FINGERPRINT',
            ''
        )
        session = get_session()
        assert isinstance(session, requests.Session)

    def test_returns_same_session_on_repeated_calls(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.tls_adapter.config.CERT_FINGERPRINT',
            ''
        )
        s1 = get_session()
        s2 = get_session()
        assert s1 is s2

    def test_mounts_pinning_adapter_when_fingerprint_set(self, mocker):
        fp = 'd' * 64
        mocker.patch(
            'sethlans_worker_agent.tls_adapter.config.CERT_FINGERPRINT',
            fp
        )
        session = get_session()
        # requests.Session stores adapters in an OrderedDict
        # The https:// adapter should be a PinningHTTPAdapter
        https_adapter = session.get_adapter('https://example.com')
        assert isinstance(https_adapter, PinningHTTPAdapter)
        assert https_adapter.expected_fingerprint == fp

    def test_verify_false_when_no_fingerprint(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.tls_adapter.config.CERT_FINGERPRINT',
            ''
        )
        session = get_session()
        assert session.verify is False

    def test_verify_false_when_fingerprint_is_none(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.tls_adapter.config.CERT_FINGERPRINT',
            None
        )
        session = get_session()
        assert session.verify is False

    def test_different_threads_get_different_sessions(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.tls_adapter.config.CERT_FINGERPRINT',
            'e' * 64
        )
        results = {}

        def thread_func(name):
            # Each thread needs its own clean state
            if hasattr(_thread_local, 'session'):
                del _thread_local.session
            results[name] = get_session()

        t1 = threading.Thread(target=thread_func, args=('t1',))
        t2 = threading.Thread(target=thread_func, args=('t2',))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results['t1'] is not results['t2']
        assert isinstance(results['t1'], requests.Session)
        assert isinstance(results['t2'], requests.Session)


# --- reset_sessions ---

class TestResetSessions:

    def setup_method(self):
        if hasattr(_thread_local, 'session'):
            del _thread_local.session

    def test_clears_current_thread_session(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.tls_adapter.config.CERT_FINGERPRINT',
            'f' * 64
        )
        get_session()  # create a session to be cleared
        reset_sessions()
        assert not hasattr(_thread_local, 'session')

    def test_next_get_session_creates_fresh_session(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.tls_adapter.config.CERT_FINGERPRINT',
            'a1' * 32
        )
        old_session = get_session()
        reset_sessions()

        mocker.patch(
            'sethlans_worker_agent.tls_adapter.config.CERT_FINGERPRINT',
            'b2' * 32
        )
        s2 = get_session()
        assert old_session is not s2
        # New session should use the updated fingerprint
        https_adapter = s2.get_adapter('https://example.com')
        assert isinstance(https_adapter, PinningHTTPAdapter)
        assert https_adapter.expected_fingerprint == 'b2' * 32

    def test_reset_when_no_session_exists_is_safe(self, mocker):
        """reset_sessions should not raise when no session exists."""
        reset_sessions()
        assert not hasattr(_thread_local, 'session')

    def test_reset_closes_session(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.tls_adapter.config.CERT_FINGERPRINT',
            ''
        )
        session = get_session()
        mock_close = mocker.patch.object(session, 'close')
        reset_sessions()
        mock_close.assert_called_once()

    def test_reset_handles_close_exception(self, mocker):
        mocker.patch(
            'sethlans_worker_agent.tls_adapter.config.CERT_FINGERPRINT',
            ''
        )
        session = get_session()
        mocker.patch.object(
            session, 'close', side_effect=RuntimeError('boom')
        )
        # Should not raise
        reset_sessions()
        assert not hasattr(_thread_local, 'session')
