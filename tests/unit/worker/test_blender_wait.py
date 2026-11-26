# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""When version sync tries a Blender install: back-off and the manager wait (BS-WK-9, BS-WK-10, D10).

``blender_wait`` with an injected clock (``_clock``) and ``blender_install.install`` faked: no sleep, no
network. The worker conftest resets the tables around every test.
"""
import types

import pytest

from sethlans_worker_agent import agent_shutdown, blender_install, blender_wait

VERSION = '4.5.8'
Outcome = blender_install.InstallOutcome


@pytest.fixture
def clock(monkeypatch):
    now = types.SimpleNamespace(t=1000.0)
    monkeypatch.setattr(blender_wait, '_clock', lambda: now.t)
    return now


@pytest.fixture
def install(mocker):
    """``blender_install.install``; each test sets ``return_value``."""
    return mocker.patch.object(blender_install, 'install')


def _result(outcome, state=None):
    return blender_install.InstallResult(outcome, exe_path='/b' if outcome is Outcome.INSTALLED else None,
                                         manager_state=state)


class TestBackoff:

    def test_a_failure_puts_the_version_in_back_off_until_cleared(self, clock):
        """BS-WK-9 (moved from version_sync): a recorded failure skips the version; clearing it (or clearing
        an unknown version) leaves it free to try."""
        assert blender_wait.is_in_backoff(VERSION) is False
        blender_wait.record_failure(VERSION)
        assert blender_wait.skip_reason(VERSION) == "in backoff period"

        blender_wait.clear_failure(VERSION)
        blender_wait.clear_failure('9.9.9')

        assert blender_wait.skip_reason(VERSION) is None

    @pytest.mark.parametrize('failures, delay', [(1, 120), (2, 240), (5, 1800)], ids=['first', 'second', 'capped'])
    def test_the_delay_doubles_and_is_capped_at_30_minutes(self, clock, failures, delay):
        """BS-WK-9 (unchanged rule): ``min(60 * 2**attempts, 1800)`` s after the last failure."""
        for _ in range(failures):
            blender_wait.record_failure(VERSION)
        start = clock.t

        clock.t = start + delay - 0.001
        assert blender_wait.is_in_backoff(VERSION) is True
        clock.t = start + delay
        assert blender_wait.is_in_backoff(VERSION) is False


class TestManagerWait:

    def test_a_fetching_manager_is_asked_again_after_60_s_and_ready_installs(self, clock, install):
        """BS-WK-10, D10: a ``queued`` answer returns at once with no failure; a sync 59 s later does not ask;
        one at 60 s asks again; a ``ready`` answer then installs and clears the entry."""
        install.return_value = _result(Outcome.WAITING, 'queued')
        start = clock.t

        assert blender_wait.attempt(VERSION) is None
        install.assert_called_once_with(VERSION, blocking=False, stop=agent_shutdown._shutdown_event,
                                        wait_for_manager=True)
        assert blender_wait.is_in_backoff(VERSION) is False  # a wait is not a failure (BS-WK-9)

        clock.t = start + 59
        assert blender_wait.attempt(VERSION) is None
        assert install.call_count == 1  # not asked

        clock.t = start + 60
        install.return_value = _result(Outcome.INSTALLED, 'ready')
        assert blender_wait.attempt(VERSION) is True
        assert install.call_count == 2
        assert blender_wait.skip_reason(VERSION) is None
        assert blender_wait._waits == {}

    @pytest.mark.parametrize('ask_at, waits', [(1799, True), (1800, False)], ids=['29m59s', '30m'])
    def test_after_30_minutes_of_fetching_the_next_attempt_goes_online(self, clock, install, ask_at, waits):
        """BS-WK-10, D10: with the manager still fetching, the attempt 30 min after its first answer no
        longer waits (``wait_for_manager=False``: the install goes online) and its success clears the entry."""
        install.return_value = _result(Outcome.WAITING, 'fetching')
        start = clock.t
        blender_wait.attempt(VERSION)
        clock.t = start + ask_at - 60
        blender_wait.attempt(VERSION)  # the manager still fetching: next ask at ``ask_at``

        clock.t = start + ask_at
        install.return_value = _result(Outcome.INSTALLED)
        assert blender_wait.attempt(VERSION) is True

        assert install.call_count == 3
        assert install.call_args.kwargs['wait_for_manager'] is waits
        assert blender_wait._waits == {}

    def test_a_version_leaving_the_download_actions_waits_no_more(self, clock):
        """BS-WK-10: ``keep_waits`` drops the wait of every version no longer to download."""
        blender_wait.record_wait(VERSION)
        blender_wait.record_wait('4.2.19')

        blender_wait.keep_waits(['4.2.19'])

        assert (blender_wait.is_waiting(VERSION), blender_wait.is_waiting('4.2.19')) == (False, True)
