# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the tls_adapter module.

Tests PinningHTTPAdapter fingerprint verification, thread-local session
management, the process pin and its table (#306), and session reset for
re-enrollment. The config store is faked at ``config_store.load_strict``;
requests against a real TLS server are in ``test_tls_adapter_loopback.py``.
"""
import threading

import pytest
import requests
import requests.adapters

from sethlans_worker_agent import config_store, tls_adapter
from sethlans_worker_agent.tls_adapter import (
    PinningHTTPAdapter,
    PinUnavailableError,
    _thread_local,
    clear_pin,
    get_session,
    reset_sessions,
    unpinned_session,
)

LOAD = 'sethlans_worker_agent.tls_adapter.config_store.load_strict'


@pytest.fixture(autouse=True)
def _clean_tls_state():
    reset_sessions()
    yield
    reset_sessions()


def _store(mocker, fingerprint='', token=None):
    """Fake one strict read: ``manager.cert_fingerprint`` and, when given, ``manager.api_token``."""
    manager = {'cert_fingerprint': fingerprint}
    if token is not None:
        manager['api_token'] = token
    return mocker.patch(LOAD, return_value={'manager': manager})


def _unreadable(mocker):
    return mocker.patch(LOAD, side_effect=config_store.ConfigReadError('could not read config.json: busy'))


def _pin_of(session):
    adapter = session.get_adapter('https://manager.local:8080')
    return adapter.expected_fingerprint if isinstance(adapter, PinningHTTPAdapter) else None


def _on_other_thread(work):
    """Run ``work()`` on a new thread; its result or exception. Its session is closed, the process pin kept."""
    seen = {}

    def run():
        try:
            seen['result'] = work()
        except Exception as error:  # noqa: BLE001 - handed back to the test
            seen['error'] = error
        finally:
            session = getattr(_thread_local, 'session', None)
            if session is not None:
                session.close()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(5)
    return seen


# --- PinningHTTPAdapter ---

class TestPinningHTTPAdapter:

    def test_stores_expected_fingerprint(self):
        fp = 'a' * 64
        adapter = PinningHTTPAdapter(expected_fingerprint=fp)
        assert adapter.expected_fingerprint == fp

    def test_init_poolmanager_sets_assert_fingerprint(self, mocker):
        fp = 'b' * 64
        adapter = PinningHTTPAdapter(expected_fingerprint=fp)
        mock_super = mocker.patch.object(requests.adapters.HTTPAdapter, 'init_poolmanager')
        adapter.init_poolmanager(1, 2, block=False)
        mock_super.assert_called_once_with(1, 2, block=False, assert_fingerprint=fp)

    def test_proxy_manager_for_sets_assert_fingerprint(self, mocker):
        fp = 'c' * 64
        adapter = PinningHTTPAdapter(expected_fingerprint=fp)
        mock_super = mocker.patch.object(requests.adapters.HTTPAdapter, 'proxy_manager_for')
        adapter.proxy_manager_for('http://proxy:8080', some_kw='val')
        mock_super.assert_called_once_with('http://proxy:8080', some_kw='val', assert_fingerprint=fp)

    def test_inherits_from_http_adapter(self):
        adapter = PinningHTTPAdapter(expected_fingerprint='x' * 64)
        assert isinstance(adapter, requests.adapters.HTTPAdapter)


# --- get_session ---

class TestGetSession:

    def test_returns_requests_session(self, mocker):
        _store(mocker)
        assert isinstance(get_session(), requests.Session)

    def test_returns_same_session_on_repeated_calls(self, mocker):
        _store(mocker)
        assert get_session() is get_session()

    def test_mounts_pinning_adapter_when_fingerprint_set(self, mocker):
        fp = 'd' * 64
        load = _store(mocker, fp, token='tok')
        session = get_session()
        load.assert_called_once_with()
        assert _pin_of(session) == fp
        assert session.verify is False

    def test_verify_false_and_no_pin_before_enrollment(self, mocker):
        _store(mocker)
        session = get_session()
        assert session.verify is False
        assert _pin_of(session) is None

    @pytest.mark.parametrize('stored', [{}, {'manager': None}, {'manager': 'x'},
                                        {'manager': {'cert_fingerprint': None}},
                                        {'manager': {'cert_fingerprint': 5}}])
    def test_odd_store_without_token_is_unpinned(self, mocker, stored):
        mocker.patch(LOAD, return_value=stored)
        assert _pin_of(get_session()) is None

    def test_different_threads_get_different_sessions(self, mocker):
        _store(mocker, 'e' * 64)
        mine = get_session()
        other = _on_other_thread(get_session)['result']
        assert other is not mine
        assert _pin_of(other) == _pin_of(mine) == 'e' * 64


class TestPinTable:
    """#306: when a request may go unpinned, and which pin it uses."""

    def test_token_without_any_pin_raises(self, mocker):
        _store(mocker, '', token='tok')
        with pytest.raises(PinUnavailableError):
            get_session()
        assert not hasattr(_thread_local, 'session')

    def test_pin_unavailable_is_a_connection_error(self):
        assert issubclass(PinUnavailableError, requests.exceptions.ConnectionError)

    def test_unreadable_store_without_any_pin_raises(self, mocker):
        _unreadable(mocker)
        with pytest.raises(PinUnavailableError):
            get_session()

    @pytest.mark.parametrize('second_read', ['empty', 'unreadable', 'token_only'])
    def test_process_pin_covers_a_failed_or_empty_read_on_another_thread(self, mocker, second_read):
        _store(mocker, 'a1' * 32, token='tok')
        get_session()  # this thread reads the pin: the process keeps it
        if second_read == 'unreadable':
            _unreadable(mocker)
        else:
            _store(mocker, '', token='tok' if second_read == 'token_only' else None)
        assert _on_other_thread(lambda: _pin_of(get_session())) == {'result': 'a1' * 32}

    def test_store_pin_replaces_the_process_pin(self, mocker):
        _store(mocker, 'a1' * 32, token='tok')
        get_session()
        _store(mocker, 'b2' * 32, token='tok')
        assert _on_other_thread(lambda: _pin_of(get_session())) == {'result': 'b2' * 32}
        assert tls_adapter._process_pin == 'b2' * 32
        assert _pin_of(get_session()) == 'a1' * 32  # this thread's pinned session keeps its pin

    def test_clear_pin_then_token_without_pin_raises(self, mocker):
        _store(mocker, 'a1' * 32, token='tok')
        get_session()
        clear_pin()
        assert tls_adapter._process_pin == ''
        _store(mocker, '', token='tok')
        assert isinstance(_on_other_thread(get_session)['error'], PinUnavailableError)

    def test_unpinned_session_is_never_used_once_a_token_is_saved(self, mocker):
        _store(mocker)
        before = get_session()
        _store(mocker, '', token='tok')  # enrolled, the pin not readable yet
        with pytest.raises(PinUnavailableError):
            get_session()
        _store(mocker, 'c3' * 32, token='tok')
        after = get_session()
        assert after is not before and _pin_of(after) == 'c3' * 32


class TestUnpinnedSession:

    def test_new_unpinned_session_each_call(self, mocker):
        load = _store(mocker, 'a1' * 32, token='tok')
        with unpinned_session() as first, unpinned_session() as second:
            assert first is not second
            assert first.verify is False and _pin_of(first) is None
        load.assert_not_called()  # it never reads the store

    def test_leaves_the_thread_session_alone(self, mocker):
        _store(mocker, 'a1' * 32, token='tok')
        pinned = get_session()
        with unpinned_session() as session:
            assert session is not pinned
        assert get_session() is pinned and _pin_of(pinned) == 'a1' * 32


# --- reset_sessions ---

class TestResetSessions:

    def test_clears_current_thread_session_and_pin(self, mocker):
        _store(mocker, 'f' * 64)
        get_session()
        reset_sessions()
        assert not hasattr(_thread_local, 'session')
        assert _thread_local.pinned is False
        assert tls_adapter._process_pin == ''

    def test_next_get_session_creates_fresh_session(self, mocker):
        _store(mocker, 'a1' * 32)
        old_session = get_session()
        reset_sessions()

        _store(mocker, 'b2' * 32)
        s2 = get_session()
        assert old_session is not s2
        assert _pin_of(s2) == 'b2' * 32

    def test_reset_when_no_session_exists_is_safe(self):
        reset_sessions()
        assert not hasattr(_thread_local, 'session')

    def test_reset_closes_session(self, mocker):
        _store(mocker)
        session = get_session()
        mock_close = mocker.patch.object(session, 'close')
        reset_sessions()
        mock_close.assert_called_once()

    def test_reset_handles_close_exception(self, mocker):
        _store(mocker)
        session = get_session()
        mocker.patch.object(session, 'close', side_effect=RuntimeError('boom'))
        reset_sessions()
        assert not hasattr(_thread_local, 'session')
