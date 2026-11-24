# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``tls_adapter`` against a real HTTPS server on loopback (#306).

The stored config is faked at ``config_store.load_strict``: a fingerprint, an
API token, or a read that fails. ``InsecureRequestWarning`` marks an unpinned
request. The pin table:

- before enrollment (no token, no pin anywhere): unpinned, one warning;
- token and no pin, or an unreadable store and no process pin:
  ``PinUnavailableError`` and nothing reaches the server;
- token and pin: pinned, no warning; a later empty or failed read on a
  thread that has no pinned session uses the process pin, no warning;
- a wrong pin: ``SSLError``; after ``clear_pin()`` a token with no pin raises.

Also: an unpinned session is upgraded on its thread once a pin is saved (one
INFO line); a pinned one is never downgraded; ``reset_sessions()`` drops the
session, the flag and the process pin; enrollment from a thread pinned to
another certificate still reaches the manager, through its own session.
"""
import logging
import threading

import pytest
import requests

from sethlans_worker_agent import config_store, enrollment_client, tls_adapter
from sethlans_worker_agent.tls_adapter import (
    PinningHTTPAdapter, PinUnavailableError, clear_pin, get_session, reset_sessions,
)
from tests.unit.worker._tls_loopback import TIMEOUT, count_insecure, serving

LOGGER = 'sethlans_worker_agent.tls_adapter'
UPGRADE_LINE = "A manager certificate fingerprint is now saved; this thread's requests are pinned to it."
WRONG_PIN = '00' * 32


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    try:
        with serving(tmp_path_factory.mktemp('tls')) as loopback:
            yield loopback
    except OSError as error:
        pytest.skip(f'cannot serve on loopback: {error}')


@pytest.fixture
def store(monkeypatch):
    """The stored ``manager`` section; ``unreadable`` makes the strict read fail. Clean TLS state around it."""
    values = {'fingerprint': '', 'token': None, 'unreadable': False}

    def load_strict():
        if values['unreadable']:
            raise config_store.ConfigReadError('could not read config.json: busy')
        manager = {'cert_fingerprint': values['fingerprint']}
        if values['token']:
            manager['api_token'] = values['token']
        return {'manager': manager}
    monkeypatch.setattr(tls_adapter.config_store, 'load_strict', load_strict)
    reset_sessions()
    yield values
    reset_sessions()


def _get(server, n=1):
    return count_insecure(lambda: get_session().get(server.url, timeout=TIMEOUT), n)


def _upgrades(caplog):
    return [r for r in caplog.records if r.name == LOGGER and r.getMessage() == UPGRADE_LINE]


def _on_other_thread(work):
    """Run ``work()`` on a new thread and close its session; the process pin is kept."""
    def run():
        try:
            work()
        finally:
            session = getattr(tls_adapter._thread_local, 'session', None)
            if session is not None:
                session.close()
    thread = threading.Thread(target=run)
    thread.start()
    thread.join(TIMEOUT)


def _pin_process(store, fingerprint):
    """Another thread reads ``fingerprint`` from the store, so the process pin is set."""
    store['fingerprint'] = fingerprint
    _on_other_thread(get_session)
    assert tls_adapter._process_pin == fingerprint


class TestWarningMeansUnpinned:

    def test_pinned_request_raises_no_insecure_warning(self, server):
        with requests.Session() as session:
            session.verify = False
            session.mount('https://', PinningHTTPAdapter(expected_fingerprint=server.fingerprint))
            assert count_insecure(lambda: session.get(server.url, timeout=TIMEOUT), 3) == 0

    def test_unpinned_request_raises_one_insecure_warning_each(self, server):
        with requests.Session() as session:
            session.verify = False
            assert count_insecure(lambda: session.get(server.url, timeout=TIMEOUT), 3) == 3

    def test_wrong_pin_fails_the_handshake(self, server):
        with requests.Session() as session:
            session.verify = False
            session.mount('https://', PinningHTTPAdapter(expected_fingerprint=WRONG_PIN))
            with pytest.raises(requests.exceptions.SSLError):
                session.get(server.url, timeout=TIMEOUT)


class TestPinTable:

    def test_before_enrollment_is_unpinned_with_one_warning(self, server, store):
        assert _get(server) == 1
        assert tls_adapter._thread_local.pinned is False

    @pytest.mark.parametrize('state', ['token', 'unreadable'])
    def test_no_pin_known_raises_and_sends_nothing(self, server, store, state):
        if state == 'token':
            store['token'] = 'tok'
        else:
            store['unreadable'] = True
        sent = len(server.hits)
        with pytest.raises(PinUnavailableError):
            get_session().get(server.url, timeout=TIMEOUT)
        assert len(server.hits) == sent

    def test_token_and_pin_raise_no_warning(self, server, store):
        store.update(fingerprint=server.fingerprint, token='tok')
        assert _get(server, n=2) == 0

    @pytest.mark.parametrize('read', ['unreadable', 'empty', 'empty_with_token'])
    def test_failed_or_empty_read_uses_the_process_pin(self, server, store, read):
        assert _get(server) == 1  # this thread's session is from before enrollment
        _pin_process(store, server.fingerprint)
        store['fingerprint'] = ''
        store['unreadable'] = read == 'unreadable'
        store['token'] = 'tok' if read == 'empty_with_token' else None

        assert _get(server, n=2) == 0
        assert tls_adapter._thread_local.pinned is True

    def test_wrong_pin_is_an_ssl_error(self, server, store):
        store.update(fingerprint=WRONG_PIN, token='tok')
        with pytest.raises(requests.exceptions.SSLError):
            get_session().get(server.url, timeout=TIMEOUT)

    def test_after_clear_pin_a_token_without_pin_raises(self, server, store):
        get_session()  # from before enrollment
        _pin_process(store, server.fingerprint)
        clear_pin()
        store.update(fingerprint='', token='tok')
        sent = len(server.hits)
        with pytest.raises(PinUnavailableError):
            get_session()
        assert len(server.hits) == sent


class TestUpgrade:

    def test_session_from_before_enrollment_is_pinned_once_the_fingerprint_is_saved(
            self, server, store, mocker, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER)
        before = get_session()
        assert _get(server) == 1
        assert get_session() is before and _upgrades(caplog) == []
        close = mocker.spy(before, 'close')

        store.update(fingerprint=server.fingerprint, token='tok')
        after = get_session()

        assert after is not before
        close.assert_called_once_with()
        assert after.get_adapter(server.url).expected_fingerprint == server.fingerprint
        assert _get(server, n=2) == 0
        assert get_session() is after and len(_upgrades(caplog)) == 1

    def test_upgraded_session_with_a_wrong_pin_fails(self, server, store):
        get_session()
        store.update(fingerprint=WRONG_PIN, token='tok')
        with pytest.raises(requests.exceptions.SSLError):
            get_session().get(server.url, timeout=TIMEOUT)

    @pytest.mark.parametrize('later', ['empty', 'unreadable', 'other_pin'])
    def test_pinned_session_is_never_changed_by_a_later_read(self, server, store, later):
        store.update(fingerprint=server.fingerprint, token='tok')
        pinned = get_session()
        store['fingerprint'] = WRONG_PIN if later == 'other_pin' else ''
        store['unreadable'] = later == 'unreadable'

        assert get_session() is pinned
        assert _get(server, n=2) == 0

    def test_pinned_from_the_start_logs_no_upgrade(self, server, store, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER)
        store['fingerprint'] = server.fingerprint
        assert _get(server) == 0 and _upgrades(caplog) == []

    def test_thread_started_after_enrollment_is_pinned_without_an_upgrade(self, server, store, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER)
        get_session()  # this thread enrolled with an unpinned session
        store.update(fingerprint=server.fingerprint, token='tok')
        seen = {}
        _on_other_thread(lambda: seen.update(adapter=get_session().get_adapter(server.url),
                                             pinned=tls_adapter._thread_local.pinned))

        assert isinstance(seen['adapter'], PinningHTTPAdapter) and seen['pinned'] is True
        assert _upgrades(caplog) == []  # only this thread upgrades, at its next call
        assert _get(server) == 0 and len(_upgrades(caplog)) == 1


class TestEnrollmentFromAPinnedThread:

    def test_enrollment_reaches_a_manager_with_another_certificate(self, server, store):
        store.update(fingerprint=WRONG_PIN, token='tok')
        pinned = get_session()  # pinned to the old manager's certificate
        sent = len(server.hits)

        warned = count_insecure(lambda: enrollment_client._post_enroll(server.url, {'nonce': 'n'}))

        assert warned == 1  # its own unpinned session; the warning is not suppressed
        assert server.hits[sent:] == ['POST']
        assert get_session() is pinned and pinned.get_adapter(server.url).expected_fingerprint == WRONG_PIN


class TestResetSessions:

    def test_reset_drops_the_session_the_flag_and_the_process_pin(self, server, store):
        store['fingerprint'] = server.fingerprint
        pinned = get_session()

        reset_sessions()

        assert not hasattr(tls_adapter._thread_local, 'session')
        assert tls_adapter._thread_local.pinned is False and tls_adapter._process_pin == ''
        store['fingerprint'] = ''  # re-enrollment clears the old pin
        assert _get(server) == 1 and get_session() is not pinned

    def test_after_reset_the_new_pin_upgrades_again(self, server, store, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER)
        store['fingerprint'] = WRONG_PIN
        get_session()
        reset_sessions()
        store['fingerprint'] = ''
        get_session()

        store['fingerprint'] = server.fingerprint

        assert _get(server) == 0 and len(_upgrades(caplog)) == 1
