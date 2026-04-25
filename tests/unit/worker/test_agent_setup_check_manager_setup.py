# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for agent_setup.check_manager_setup_complete (issue #126).

Verifies state-transition logging:
- First-True observation is silent.
- First-False observation logs the idle message once.
- True->False transition logs idle once.
- False->True transition logs the resume message once.
- Repeated False observations log only once.
"""
import logging

import pytest

from sethlans_worker_agent import agent_setup, system_monitor

# Logger name used in agent_setup.py: ``sethlans_worker_agent.agent_setup``
_AGENT_SETUP_LOGGER = 'sethlans_worker_agent.agent_setup'

_IDLE_SUBSTR = 'Manager setup not yet complete; idling'
_RESUME_SUBSTR = 'Manager setup is complete; resuming normal operation'


@pytest.fixture(autouse=True)
def _reset_transition_state():
    """Reset transition-tracking and system_monitor state between tests."""
    agent_setup._last_known_setup_complete = None
    system_monitor._manager_setup_complete = True
    yield
    agent_setup._last_known_setup_complete = None
    system_monitor._manager_setup_complete = True


def _idle_records(caplog):
    return [r for r in caplog.records if _IDLE_SUBSTR in r.getMessage()]


def _resume_records(caplog):
    return [r for r in caplog.records if _RESUME_SUBSTR in r.getMessage()]


class TestCheckManagerSetupCompleteTransitions:

    def test_first_true_observation_is_silent(self, caplog):
        system_monitor._manager_setup_complete = True
        with caplog.at_level(logging.INFO, logger=_AGENT_SETUP_LOGGER):
            result = agent_setup.check_manager_setup_complete()
        assert result is True
        assert _idle_records(caplog) == []
        assert _resume_records(caplog) == []

    def test_first_false_observation_logs_idle(self, caplog):
        system_monitor._manager_setup_complete = False
        with caplog.at_level(logging.INFO, logger=_AGENT_SETUP_LOGGER):
            result = agent_setup.check_manager_setup_complete()
        assert result is False
        assert len(_idle_records(caplog)) == 1
        assert _resume_records(caplog) == []

    def test_true_to_false_transition_logs_idle(self, caplog):
        # Initial True observation (silent).
        system_monitor._manager_setup_complete = True
        with caplog.at_level(logging.INFO, logger=_AGENT_SETUP_LOGGER):
            agent_setup.check_manager_setup_complete()
            assert _idle_records(caplog) == []

            # Now flip False -> single idle log.
            system_monitor._manager_setup_complete = False
            agent_setup.check_manager_setup_complete()

        assert len(_idle_records(caplog)) == 1
        assert _resume_records(caplog) == []

    def test_false_to_true_transition_logs_resume(self, caplog):
        system_monitor._manager_setup_complete = False
        with caplog.at_level(logging.INFO, logger=_AGENT_SETUP_LOGGER):
            agent_setup.check_manager_setup_complete()
            assert len(_idle_records(caplog)) == 1

            # Flip back to True -> resume log.
            system_monitor._manager_setup_complete = True
            agent_setup.check_manager_setup_complete()

        assert len(_resume_records(caplog)) == 1
        # Idle log count unchanged after the resume.
        assert len(_idle_records(caplog)) == 1

    def test_repeated_false_observations_log_only_once(self, caplog):
        system_monitor._manager_setup_complete = False
        with caplog.at_level(logging.INFO, logger=_AGENT_SETUP_LOGGER):
            for _ in range(3):
                agent_setup.check_manager_setup_complete()

        assert len(_idle_records(caplog)) == 1
