# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``worker/sethlans_worker_agent/wizard.py`` — unattended path.

Covers the first-run enrollment wizard in headless / no-TTY mode:

* Missing ``SETHLANS_WORKER_ENROLLMENT_KEY`` → exit 4
* Missing host env vars AND no multicast announcements → exit 4
* Four consecutive failures exhaust ``_BACKOFF_SCHEDULE`` → exit 4
  (AC-31a: exponential backoff 1s → 3s → 9s → 27s, then stop)
* Recovery after two transient failures → exit 0
* AC-31c: hostname comes from ``hardware_detection.HOSTNAME``
* Manager URL built from ``SETHLANS_MANAGER_HOST`` / ``PORT``

``time.sleep`` is mocked so the backoff tests finish instantly.
The interactive path lives in ``test_wizard.py`` so each file stays
under the 300-line cap.
"""

import pytest

from sethlans_worker_agent import wizard
from sethlans_worker_agent.enrollment_client import (
    EnrollmentError,
    InvalidKeyError,
)
from tests.unit.worker._wizard_common import VALID_RESULT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_discover(mocker):
    return mocker.patch(
        "sethlans_worker_agent.wizard.MulticastListener.discover",
        return_value={},
    )


@pytest.fixture
def mock_config_store_set(mocker):
    return mocker.patch(
        "sethlans_worker_agent.wizard.config_store.set_many",
    )


@pytest.fixture
def mock_enroll(mocker):
    return mocker.patch(
        "sethlans_worker_agent.wizard.enrollment_client.enroll",
        return_value=dict(VALID_RESULT),
    )


@pytest.fixture
def mock_hostname(mocker):
    mocker.patch(
        "sethlans_worker_agent.wizard.hardware_detection.HOSTNAME",
        "test-host-xyz",
    )


@pytest.fixture
def no_tty(mocker):
    mocker.patch(
        "sethlans_worker_agent.wizard.sys.stdin.isatty",
        return_value=False,
    )


@pytest.fixture
def mock_sleep(mocker):
    """Mock ``time.sleep`` so backoff tests run instantly."""
    return mocker.patch("sethlans_worker_agent.wizard.time.sleep")


@pytest.fixture
def clean_env(monkeypatch):
    """Remove SETHLANS_* env vars the unattended path inspects."""
    for var in (
        "SETHLANS_WORKER_ENROLLMENT_KEY",
        "SETHLANS_MANAGER_HOST",
        "SETHLANS_MANAGER_PORT",
        "SETHLANS_MANAGER_ID",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Missing env vars
# ---------------------------------------------------------------------------


class TestUnattendedMissingEnv:
    def test_missing_enrollment_key_returns_4(
        self, no_tty, clean_env, mock_config_store_set,
    ):
        rc = wizard.run_wizard()
        assert rc == wizard.WIZARD_UNATTENDED_FAILED
        mock_config_store_set.assert_not_called()

    def test_missing_host_and_no_multicast_returns_4(
        self, no_tty, clean_env, mock_discover,
        mock_config_store_set, monkeypatch,
    ):
        monkeypatch.setenv(
            "SETHLANS_WORKER_ENROLLMENT_KEY", "4F9XK2PBQ7M3N8RT",
        )
        mock_discover.return_value = {}
        rc = wizard.run_wizard()
        assert rc == wizard.WIZARD_UNATTENDED_FAILED
        mock_config_store_set.assert_not_called()


# ---------------------------------------------------------------------------
# Exponential backoff (AC-31a)
# ---------------------------------------------------------------------------


class TestUnattendedBackoff:
    def test_all_failures_exhaust_schedule_returns_4(
        self, no_tty, clean_env, mock_enroll,
        mock_sleep, mock_config_store_set, mock_hostname, monkeypatch,
    ):
        """Every attempt fails → the _BACKOFF_SCHEDULE runs out → exit 4.

        The schedule is ``(0, 1, 3, 9, 27)`` so the non-zero sleeps
        sent to ``time.sleep`` must be exactly ``[1, 3, 9, 27]``.
        """
        monkeypatch.setenv(
            "SETHLANS_WORKER_ENROLLMENT_KEY", "4F9XK2PBQ7M3N8RT",
        )
        monkeypatch.setenv("SETHLANS_MANAGER_HOST", "10.0.0.5")
        monkeypatch.setenv("SETHLANS_MANAGER_PORT", "8080")
        mock_enroll.side_effect = InvalidKeyError("Bad key.")

        rc = wizard.run_wizard()

        assert rc == wizard.WIZARD_UNATTENDED_FAILED
        assert mock_enroll.call_count == len(wizard._BACKOFF_SCHEDULE)
        expected_sleeps = [d for d in wizard._BACKOFF_SCHEDULE if d]
        slept = [c.args[0] for c in mock_sleep.call_args_list]
        assert slept == expected_sleeps
        mock_config_store_set.assert_not_called()

    def test_success_on_third_attempt_returns_0(
        self, no_tty, clean_env, mock_enroll,
        mock_sleep, mock_config_store_set, mock_hostname, monkeypatch,
    ):
        monkeypatch.setenv(
            "SETHLANS_WORKER_ENROLLMENT_KEY", "4F9XK2PBQ7M3N8RT",
        )
        monkeypatch.setenv("SETHLANS_MANAGER_HOST", "10.0.0.5")
        monkeypatch.setenv("SETHLANS_MANAGER_PORT", "8080")
        mock_enroll.side_effect = [
            EnrollmentError("transient"),
            EnrollmentError("transient"),
            dict(VALID_RESULT),
        ]

        rc = wizard.run_wizard()

        assert rc == wizard.WIZARD_OK
        assert mock_enroll.call_count == 3
        # set_many receives all pairs in a single atomic call.
        mock_config_store_set.assert_called_once()
        pairs = dict(mock_config_store_set.call_args[0][0])
        assert pairs["manager.api_token"] == VALID_RESULT["api_token"]
        assert pairs["enrollment.wizard_complete"] is True


# ---------------------------------------------------------------------------
# Hostname source (AC-31c) and manager URL construction
# ---------------------------------------------------------------------------


class TestUnattendedHostnameSource:
    def test_unattended_hostname_comes_from_hardware_detection(
        self, no_tty, clean_env, mock_enroll, mock_sleep,
        mock_config_store_set, mocker, monkeypatch,
    ):
        """AC-31c: unattended path also pulls HOSTNAME from hardware_detection."""
        mocker.patch(
            "sethlans_worker_agent.wizard.hardware_detection.HOSTNAME",
            "unattended-host-xyz",
        )
        monkeypatch.setenv(
            "SETHLANS_WORKER_ENROLLMENT_KEY", "4F9XK2PBQ7M3N8RT",
        )
        monkeypatch.setenv("SETHLANS_MANAGER_HOST", "10.0.0.5")
        monkeypatch.setenv("SETHLANS_MANAGER_PORT", "8080")

        rc = wizard.run_wizard()

        assert rc == wizard.WIZARD_OK
        args, _ = mock_enroll.call_args
        assert args[2] == "unattended-host-xyz"

    def test_unattended_manager_url_built_from_env(
        self, no_tty, clean_env, mock_enroll, mock_sleep,
        mock_config_store_set, mock_hostname, monkeypatch,
    ):
        monkeypatch.setenv(
            "SETHLANS_WORKER_ENROLLMENT_KEY", "4F9XK2PBQ7M3N8RT",
        )
        monkeypatch.setenv("SETHLANS_MANAGER_HOST", "10.0.0.77")
        monkeypatch.setenv("SETHLANS_MANAGER_PORT", "8443")

        rc = wizard.run_wizard()

        assert rc == wizard.WIZARD_OK
        args, _ = mock_enroll.call_args
        assert args[0] == "https://10.0.0.77:8443/api/"
