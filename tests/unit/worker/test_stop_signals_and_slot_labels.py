# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shutdown signals the agent listens to, and how a kept slot is shown.

Blender runs in its own session, so every signal that ends the worker must
reach the worker's shutdown handler, which stops Blender. That includes
the hangup sent when a terminal or remote session closes, unless hangups
were already ignored when the worker started (nohup), which is kept.
"""
import signal
from types import SimpleNamespace

import pytest

from sethlans_worker_agent import agent
from sethlans_worker_agent.web_ui.status import _gpu_allocation_labels


class _Stop(Exception):
    """Raised by the patched logger to leave main() right after signal setup."""


def _registered_signals(mocker):
    register = mocker.patch.object(agent.signal, 'signal')
    mocker.patch.object(agent, '_parse_args', return_value=SimpleNamespace(loglevel='INFO'))
    mocker.patch.object(agent, 'configure_logging')
    mocker.patch.object(agent.logger, 'info', side_effect=_Stop)
    with pytest.raises(_Stop):
        agent.main([])
    return {call.args[0]: call.args[1] for call in register.call_args_list}


def _hangup_disposition(mocker, disposition):
    """Give the platform a SIGHUP (number 1) whose inherited disposition is ``disposition``.

    ``getsignal`` is patched too: the real one rejects a made-up signal number.
    """
    mocker.patch.object(agent.signal, 'SIGHUP', 1, create=True)
    return mocker.patch.object(agent.signal, 'getsignal', return_value=disposition)


def _some_other_handler(signum, frame):
    """A handler a parent process or library installed before the worker."""


@pytest.mark.parametrize('disposition', [signal.SIG_DFL, _some_other_handler, None],
                         ids=['default', 'other-handler', 'not-set-from-python'])
def test_hangup_is_a_shutdown_where_the_platform_has_it(mocker, disposition):
    getsignal = _hangup_disposition(mocker, disposition)

    registered = _registered_signals(mocker)

    getsignal.assert_called_once_with(1)
    assert registered[1] is agent._shutdown_handler
    assert registered[signal.SIGINT] is agent._shutdown_handler
    assert registered[signal.SIGTERM] is agent._shutdown_handler


def test_ignored_hangup_stays_ignored(mocker):
    """Started with nohup (SIGHUP already ignored): closing the terminal must not stop the worker (#254)."""
    getsignal = _hangup_disposition(mocker, signal.SIG_IGN)

    registered = _registered_signals(mocker)

    assert 1 not in registered
    assert registered == {signal.SIGINT: agent._shutdown_handler, signal.SIGTERM: agent._shutdown_handler}
    getsignal.assert_called_once_with(1)


def test_no_hangup_handler_where_the_platform_has_none(mocker, monkeypatch):
    monkeypatch.delattr(agent.signal, 'SIGHUP', raising=False)
    getsignal = mocker.patch.object(agent.signal, 'getsignal')

    registered = _registered_signals(mocker)

    getsignal.assert_not_called()
    assert registered == {signal.SIGINT: agent._shutdown_handler, signal.SIGTERM: agent._shutdown_handler}


def test_slot_owners_are_job_ids_with_string_keys():
    assert _gpu_allocation_labels({0: 12, 1: 7}) == {'0': 12, '1': 7}


def test_kept_slot_is_shown_under_its_job():
    assert _gpu_allocation_labels({0: -8, 1: 3}) == {'0': '7 (held until restart)', '1': 3}
