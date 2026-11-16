# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shutdown signals the agent listens to, and how a kept slot is shown.

Blender runs in its own session, so every signal that ends the worker must
reach the worker's shutdown handler, which stops Blender. That includes
the hangup sent when a terminal or remote session closes.
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


def test_hangup_is_a_shutdown_where_the_platform_has_it(mocker):
    mocker.patch.object(agent.signal, 'SIGHUP', 1, create=True)

    registered = _registered_signals(mocker)

    assert registered[1] is agent._shutdown_handler
    assert registered[signal.SIGINT] is agent._shutdown_handler
    assert registered[signal.SIGTERM] is agent._shutdown_handler


def test_no_hangup_handler_where_the_platform_has_none(mocker, monkeypatch):
    monkeypatch.delattr(agent.signal, 'SIGHUP', raising=False)

    registered = _registered_signals(mocker)

    assert set(registered) == {signal.SIGINT, signal.SIGTERM}


def test_slot_owners_are_job_ids_with_string_keys():
    assert _gpu_allocation_labels({0: 12, 1: 7}) == {'0': 12, '1': 7}


def test_kept_slot_is_shown_under_its_job():
    assert _gpu_allocation_labels({0: -8, 1: 3}) == {'0': '7 (held until restart)', '1': 3}
