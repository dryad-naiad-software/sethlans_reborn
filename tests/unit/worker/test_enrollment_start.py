# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Enrollment when the agent starts (``enrollment_start``; setup-rebuild FR-WK-6, FR-WK-7, FR-DK-5).

The shutdown event is a fake whose ``wait`` records its timeout and never
sleeps. The saved token, the enrollment (``setup_cli.enroll.enroll_pairs``,
the function ``--setup-apply`` uses too), ``config_store.set_many``, the
config reload and the manager broadcasts are replaced.
"""
import logging

import pytest

from sethlans_worker_agent import config_store, enrollment_client, enrollment_start
from sethlans_worker_agent.setup_cli import enroll

KEY = 'QX7M-4F9K-2PBR-N8TW'
PAIRS = [('manager.api_token', 'token'), ('manager.manager_id', 'id-1')]


class FakeEvent:
    """A shutdown event that is set after ``stop_after`` waits; ``wait`` never blocks."""

    def __init__(self, stop_after=None):
        self.waits = []
        self._stop_after = stop_after

    def is_set(self):
        return self._stop_after is not None and len(self.waits) >= self._stop_after

    def wait(self, timeout):
        self.waits.append(timeout)
        return self.is_set()


@pytest.fixture
def start(monkeypatch, mocker):
    """No token, no key, no manager variables; the collaborators replaced. Returns the mocks.

    A save makes the token readable; a reload makes it live in ``config.API_TOKEN``.
    """
    for name in (enrollment_start.ENV_KEY, 'SETHLANS_MANAGER_HOST', 'SETHLANS_MANAGER_PORT', 'SETHLANS_MANAGER_ID'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(enrollment_start, '_key', None)
    monkeypatch.setattr(enrollment_start.config, 'API_TOKEN', None)
    monkeypatch.setattr(enrollment_start.socket, 'gethostname', lambda: 'render-node')
    mocks = mocker.MagicMock()
    mocks.get = mocker.patch.object(config_store, 'get', return_value=None)
    mocks.set_many = mocker.patch.object(
        config_store, 'set_many', side_effect=lambda pairs, **_kwargs: setattr(mocks.get, 'return_value', 'token'))
    mocks.reload = mocker.patch.object(
        enrollment_start, '_reload_config', side_effect=lambda: setattr(enrollment_start.config, 'API_TOKEN', 'token'))
    mocks.enroll = mocker.patch.object(enroll, 'enroll_pairs', return_value=({'manager_id': 'id-1'}, list(PAIRS)))
    return mocks


class TestNoKey:

    def test_logs_not_enrolled_and_checks_every_60_s_until_shutdown(self, start, caplog):
        """FR-WK-7: no token and no key: "not enrolled; run setup", a check every 60 s, no exit."""
        event = FakeEvent(stop_after=2)
        with caplog.at_level(logging.WARNING, logger=enrollment_start.__name__):
            assert enrollment_start.wait_until_enrolled(event) == enrollment_start.SHUTDOWN
        assert event.waits == [60, 60]
        lines = [r.getMessage() for r in caplog.records if r.name == enrollment_start.__name__]
        assert len(lines) == 2 and all('not enrolled; run setup' in line for line in lines)
        start.enroll.assert_not_called()

    def test_a_token_saved_by_setup_meanwhile_is_picked_up(self, start):
        """FR-WK-7: after ``--setup-apply`` saved a token, the next check enrolls and reloads the config."""
        start.get.side_effect = [None, 'token-from-setup']
        event = FakeEvent()
        assert enrollment_start.wait_until_enrolled(event) == enrollment_start.ENROLLED
        assert event.waits == [60]
        start.reload.assert_called_once_with()

    def test_a_key_set_in_os_environ_after_take_key_is_never_read(self, start, monkeypatch):
        """FR-WK-6, FR-SEC-6: only ``take_key`` reads the variable; the 60 s loop never looks at os.environ."""
        enrollment_start.take_key()
        monkeypatch.setenv(enrollment_start.ENV_KEY, KEY)
        assert enrollment_start.wait_until_enrolled(FakeEvent(stop_after=2)) == enrollment_start.SHUTDOWN
        start.enroll.assert_not_called()

    @pytest.mark.parametrize('live_after, stop_after, expected, waits', [
        ([False, True], None, enrollment_start.ENROLLED, [60]),
        ([False], 1, enrollment_start.SHUTDOWN, [60]),
    ], ids=['loaded-on-the-next-look', 'shutdown-while-waiting'])
    def test_saved_token_counts_only_once_the_reload_made_it_live(self, start, live_after, stop_after, expected,
                                                                  waits):
        """FR-WK-7, chunk 4 review (concurrency): a saved token is ``ENROLLED`` only when ``config.API_TOKEN`` holds
        it after the reload (the file may be mid-replace); else it looks again after an interruptible 60 s wait.
        FR-SEC-6: a key taken at start is not used and not kept in memory once a saved token is found."""
        assert enrollment_start.take_key({enrollment_start.ENV_KEY: KEY}) is True
        start.get.return_value = 'token'
        results = iter(live_after)
        start.reload.side_effect = lambda: setattr(enrollment_start.config, 'API_TOKEN',
                                                   'token' if next(results) else None)
        event = FakeEvent(stop_after=stop_after)
        assert enrollment_start.wait_until_enrolled(event) == expected
        assert event.waits == waits and start.reload.call_count == len(live_after)
        assert enrollment_start._key is None
        start.enroll.assert_not_called()


@pytest.mark.parametrize('environ, taken', [
    ({enrollment_start.ENV_KEY: KEY}, True),
    ({enrollment_start.ENV_KEY: ''}, False),
    ({enrollment_start.ENV_KEY: ' \t '}, False),
    ({}, False),
], ids=['set', 'empty', 'blank', 'absent'])
def test_take_key_removes_the_variable_and_keeps_only_a_real_key(start, environ, taken):
    """FR-WK-6, FR-SEC-6: ``take_key`` pops the key out of the environment (no child inherits it); empty or blank
    counts as unset."""
    environ = dict(environ, PATH='kept')
    assert enrollment_start.take_key(environ) is taken
    assert environ == {'PATH': 'kept'}
    assert enrollment_start._key == (KEY if taken else None)


class TestWithKey:

    @pytest.fixture
    def keyed(self, start, monkeypatch):
        monkeypatch.setenv(enrollment_start.ENV_KEY, KEY)
        monkeypatch.setenv('SETHLANS_MANAGER_HOST', '10.0.0.5')
        monkeypatch.setenv('SETHLANS_MANAGER_PORT', '8443')
        assert enrollment_start.take_key() is True  # agent.main takes the key out of os.environ at start
        return start

    def test_enrolls_through_the_setup_apply_function(self, keyed):
        """FR-WK-6: the key enrolls at start through ``enroll.enroll_pairs`` and saves its pairs in one write."""
        event = FakeEvent()
        assert enrollment_start.wait_until_enrolled(event) == enrollment_start.ENROLLED
        keyed.enroll.assert_called_once_with('10.0.0.5', 8443, KEY, 'render-node')
        keyed.set_many.assert_called_once_with(PAIRS)
        keyed.reload.assert_called_once_with()
        assert event.waits == []
        assert enrollment_start._key is None  # FR-SEC-6: dropped from memory once used

    def test_every_try_failing_is_exit_4_with_nothing_saved(self, keyed, caplog):
        """FR-WK-6, FR-DK-5: five tries 0, 1, 3, 9, 27 s apart, then ``ENROLL_FAILED``; the key is never logged
        and is dropped from memory (FR-SEC-6)."""
        keyed.enroll.side_effect = enrollment_client.InvalidKeyError('The enrollment key is not valid.')
        event = FakeEvent()
        with caplog.at_level(logging.DEBUG, logger=enrollment_start.__name__):
            assert enrollment_start.wait_until_enrolled(event) == enrollment_start.ENROLL_FAILED
        assert event.waits == [1, 3, 9, 27] and keyed.enroll.call_count == 5
        keyed.set_many.assert_not_called()
        assert KEY not in caplog.text
        assert enrollment_start._key is None

    @pytest.mark.parametrize('error', [config_store.ConfigLockTimeoutError('lock busy'),
                                       OSError(28, 'No space left on device')], ids=['lock-timeout', 'oserror'])
    def test_an_answer_that_cannot_be_saved_is_a_failed_try(self, keyed, caplog, error):
        """FR-WK-6, chunk 4 review (concurrency): a good enroll whose save fails is a failed try (one WARNING each,
        no traceback); five of them end with exit 4."""
        keyed.set_many.side_effect = error
        event = FakeEvent()
        with caplog.at_level(logging.DEBUG, logger=enrollment_start.__name__):
            assert enrollment_start.wait_until_enrolled(event) == enrollment_start.ENROLL_FAILED
        assert event.waits == [1, 3, 9, 27] and keyed.set_many.call_count == 5
        records = [r for r in caplog.records if r.name == enrollment_start.__name__]
        warnings = [r.getMessage() for r in records if r.levelno == logging.WARNING]
        assert len(warnings) == 5 and all('could not be saved' in line for line in warnings)
        assert all(r.exc_info is None for r in records)
        keyed.reload.assert_not_called()

    def test_shutdown_during_the_back_off_stops_at_once(self, keyed):
        """FR-WK-6: a shutdown while waiting between tries ends the start without another try."""
        keyed.enroll.side_effect = enrollment_client.EnrollmentError('x')
        assert enrollment_start.wait_until_enrolled(FakeEvent(stop_after=1)) == enrollment_start.SHUTDOWN
        assert keyed.enroll.call_count == 1


class FakeListener:
    found = {}

    def discover(self):
        return FakeListener.found


ONE = {'id-1': {'manager_id': 'id-1', 'ip': '10.0.0.1', 'port': 8080}}
TWO = dict(ONE, **{'id-2': {'manager_id': 'id-2', 'host': 'lab.example', 'port': '9443'}})


@pytest.mark.parametrize('env, found, expected', [
    ({'SETHLANS_MANAGER_HOST': 'm.example', 'SETHLANS_MANAGER_PORT': 'abc'}, {}, ('m.example', 8080)),
    ({}, ONE, ('10.0.0.1', 8080)),
    ({'SETHLANS_MANAGER_ID': 'id-2'}, TWO, ('lab.example', 9443)),
    ({}, TWO, None),
    ({}, {}, None),
], ids=['env-bad-port', 'one-broadcast', 'id-picks-one', 'several-no-id', 'none'])
def test_manager_from_the_environment_or_the_broadcasts(start, monkeypatch, env, found, expected):
    """FR-WK-6: the Docker worker's manager comes from its variables, else from one (or the named) broadcast."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(enrollment_start, 'MulticastListener', FakeListener)
    monkeypatch.setattr(FakeListener, 'found', found)
    assert enrollment_start._manager_from_env_or_broadcast() == expected
