# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``worker/sethlans_worker_agent/wizard.py`` — interactive path.

Covers happy path, multi-manager selection, no-managers, retry/decline,
Ctrl+C during getpass, and AC-31c hostname source. The unattended path
lives in ``test_wizard_unattended.py`` so each file stays under 300 lines.
"""

import pytest

from sethlans_worker_agent import wizard
from sethlans_worker_agent.enrollment_client import InvalidKeyError
from tests.unit.worker._wizard_common import (
    ANNOUNCEMENT_ONE,
    ANNOUNCEMENT_TWO,
    VALID_RESULT,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_discover(mocker):
    return mocker.patch(
        "sethlans_worker_agent.wizard.MulticastListener.discover",
        return_value=dict(ANNOUNCEMENT_ONE),
    )


@pytest.fixture
def mock_config_store_set(mocker):
    return mocker.patch("sethlans_worker_agent.wizard.config_store.set_many")


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
def tty(mocker):
    mocker.patch(
        "sethlans_worker_agent.wizard.sys.stdin.isatty",
        return_value=True,
    )


class TestInteractiveHappyPath:
    def test_single_manager_accepted_and_enrolled(
        self, mocker, tty, mock_discover,
        mock_enroll, mock_config_store_set, mock_hostname,
    ):
        """One manager discovered → user accepts → config persisted → exit 0."""
        mocker.patch(
            "sethlans_worker_agent.wizard.input",
            return_value="",  # Hit Enter → default "Y"
        )
        mocker.patch(
            "sethlans_worker_agent.wizard.getpass.getpass",
            return_value="4F9X-K2PB-Q7M3-N8RT",
        )

        rc = wizard.run_wizard()

        assert rc == wizard.WIZARD_OK
        mock_enroll.assert_called_once()
        args, _kwargs = mock_enroll.call_args
        # enroll(manager_url, key, hostname)
        assert args[1] == "4F9X-K2PB-Q7M3-N8RT"
        assert args[2] == "test-host-xyz"
        assert args[0].startswith("https://10.0.0.1:8080")

    def test_config_store_set_receives_credential_triple(
        self, mocker, tty, mock_discover,
        mock_enroll, mock_config_store_set, mock_hostname,
    ):
        mocker.patch("sethlans_worker_agent.wizard.input", return_value="y")
        mocker.patch(
            "sethlans_worker_agent.wizard.getpass.getpass",
            return_value="4F9XK2PBQ7M3N8RT",
        )

        rc = wizard.run_wizard()

        assert rc == wizard.WIZARD_OK
        # set_many is called once with all pairs in a single write.
        mock_config_store_set.assert_called_once()
        pairs = dict(mock_config_store_set.call_args[0][0])
        assert pairs["manager.api_token"] == VALID_RESULT["api_token"]
        assert pairs["manager.cert_fingerprint"] == (
            VALID_RESULT["cert_fingerprint"]
        )
        assert pairs["manager.manager_id"] == VALID_RESULT["manager_id"]
        assert pairs["enrollment.wizard_complete"] is True


class TestInteractiveMultipleManagers:
    def test_user_picks_second_manager(
        self, mocker, tty, mock_discover,
        mock_enroll, mock_config_store_set, mock_hostname,
    ):
        mock_discover.return_value = dict(ANNOUNCEMENT_TWO)
        mocker.patch(
            "sethlans_worker_agent.wizard.input",
            return_value="2",
        )
        mocker.patch(
            "sethlans_worker_agent.wizard.getpass.getpass",
            return_value="4F9XK2PBQ7M3N8RT",
        )

        rc = wizard.run_wizard()

        assert rc == wizard.WIZARD_OK
        args, _ = mock_enroll.call_args
        # Second manager is at 10.0.0.2.
        assert "10.0.0.2" in args[0]

    def test_invalid_selection_then_valid(
        self, mocker, tty, mock_discover,
        mock_enroll, mock_config_store_set, mock_hostname,
    ):
        """Non-integer then out-of-range then a valid pick."""
        mock_discover.return_value = dict(ANNOUNCEMENT_TWO)
        mocker.patch(
            "sethlans_worker_agent.wizard.input",
            side_effect=["nope", "9", "1"],
        )
        mocker.patch(
            "sethlans_worker_agent.wizard.getpass.getpass",
            return_value="4F9XK2PBQ7M3N8RT",
        )

        rc = wizard.run_wizard()

        assert rc == wizard.WIZARD_OK
        args, _ = mock_enroll.call_args
        assert "10.0.0.1" in args[0]


class TestInteractiveUnhappyPaths:
    def test_no_managers_discovered_returns_2(
        self, tty, mock_discover, mock_config_store_set,
    ):
        mock_discover.return_value = {}
        rc = wizard.run_wizard()
        assert rc == wizard.WIZARD_NO_MANAGERS
        mock_config_store_set.assert_not_called()

    def test_user_declines_single_manager_returns_3(
        self, mocker, tty, mock_discover, mock_config_store_set,
    ):
        mocker.patch(
            "sethlans_worker_agent.wizard.input", return_value="n",
        )
        rc = wizard.run_wizard()
        assert rc == wizard.WIZARD_USER_DECLINED
        mock_config_store_set.assert_not_called()

    def test_user_declines_retry_returns_3(
        self, mocker, tty, mock_discover,
        mock_enroll, mock_config_store_set, mock_hostname,
    ):
        """First enrollment fails → user declines retry → exit 3."""
        mocker.patch(
            "sethlans_worker_agent.wizard.input",
            side_effect=["y", "n"],  # Use manager → fail → decline retry
        )
        mocker.patch(
            "sethlans_worker_agent.wizard.getpass.getpass",
            return_value="4F9XK2PBQ7M3N8RT",
        )
        mock_enroll.side_effect = InvalidKeyError("Bad key.")

        rc = wizard.run_wizard()

        assert rc == wizard.WIZARD_USER_DECLINED
        mock_config_store_set.assert_not_called()

    def test_retry_succeeds_on_second_attempt(
        self, mocker, tty, mock_discover,
        mock_enroll, mock_config_store_set, mock_hostname,
    ):
        mocker.patch(
            "sethlans_worker_agent.wizard.input",
            side_effect=["y", "y"],  # Use manager → retry after failure
        )
        mocker.patch(
            "sethlans_worker_agent.wizard.getpass.getpass",
            side_effect=["bad-key", "4F9XK2PBQ7M3N8RT"],
        )
        mock_enroll.side_effect = [
            InvalidKeyError("Bad key."),
            dict(VALID_RESULT),
        ]

        rc = wizard.run_wizard()

        assert rc == wizard.WIZARD_OK
        assert mock_enroll.call_count == 2

    def test_ctrl_c_during_getpass_returns_5(
        self, mocker, tty, mock_discover, mock_config_store_set, capsys,
    ):
        mocker.patch(
            "sethlans_worker_agent.wizard.input", return_value="y",
        )
        mocker.patch(
            "sethlans_worker_agent.wizard.getpass.getpass",
            side_effect=KeyboardInterrupt(),
        )

        rc = wizard.run_wizard()

        assert rc == wizard.WIZARD_CANCELLED
        out = capsys.readouterr().out
        assert "Enrollment cancelled." in out
        mock_config_store_set.assert_not_called()


class TestHostnameSourceAC31c:
    def test_interactive_hostname_comes_from_hardware_detection(
        self, mocker, tty, mock_discover,
        mock_enroll, mock_config_store_set,
    ):
        """AC-31c: both interactive and unattended pull HOSTNAME from the
        hardware_detection module so tests can pin a single value."""
        mocker.patch(
            "sethlans_worker_agent.wizard.hardware_detection.HOSTNAME",
            "pinned-host-xyz",
        )
        mocker.patch(
            "sethlans_worker_agent.wizard.input", return_value="y",
        )
        mocker.patch(
            "sethlans_worker_agent.wizard.getpass.getpass",
            return_value="4F9XK2PBQ7M3N8RT",
        )

        rc = wizard.run_wizard()

        assert rc == wizard.WIZARD_OK
        args, _ = mock_enroll.call_args
        assert args[2] == "pinned-host-xyz"


def test_wizard_exit_codes_are_distinct():
    codes = {
        wizard.WIZARD_OK,
        wizard.WIZARD_NO_MANAGERS,
        wizard.WIZARD_USER_DECLINED,
        wizard.WIZARD_UNATTENDED_FAILED,
        wizard.WIZARD_CANCELLED,
    }
    assert len(codes) == 5
