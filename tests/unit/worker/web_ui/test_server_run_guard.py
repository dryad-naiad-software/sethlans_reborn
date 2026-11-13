# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``_run_server`` in
``worker/sethlans_worker_agent/web_ui/server.py``.

The wrapper suppresses the Waitress fd-teardown race only while
``stop_server`` is shutting down: ``EBADF`` on macOS/Linux (#120) and
``ENOTSOCK`` / ``[WinError 10038]`` on Windows (#213). Every other
case must propagate so the daemon thread surfaces real crashes.

``server.run`` is mocked to raise; no real sockets are created.
"""

import errno
import sys
from unittest.mock import MagicMock

import pytest

from sethlans_worker_agent.web_ui import server

RACE_ERRNOS = [
    pytest.param(errno.EBADF, id="EBADF"),
    pytest.param(errno.ENOTSOCK, id="ENOTSOCK"),
]


def _raising_server(err_no):
    """Return a fake Waitress server whose ``run`` raises ``OSError``."""
    fake = MagicMock()
    fake.run.side_effect = OSError(err_no, "simulated select() failure")
    return fake


@pytest.fixture
def shutting_down(monkeypatch):
    """Simulate ``stop_server`` having set the shutdown flag."""
    monkeypatch.setattr(server, "_shutting_down", True)


@pytest.fixture
def not_shutting_down(monkeypatch):
    """Simulate normal operation (no shutdown in progress)."""
    monkeypatch.setattr(server, "_shutting_down", False)


@pytest.mark.parametrize("err_no", RACE_ERRNOS)
def test_race_errno_suppressed_during_shutdown(shutting_down, err_no):
    fake = _raising_server(err_no)

    assert server._run_server(fake) is None
    fake.run.assert_called_once_with()


@pytest.mark.parametrize("err_no", RACE_ERRNOS)
def test_race_errno_reraised_when_not_shutting_down(
    not_shutting_down, err_no,
):
    fake = _raising_server(err_no)

    with pytest.raises(OSError) as exc_info:
        server._run_server(fake)
    assert exc_info.value.errno == err_no


def test_other_errno_reraised_during_shutdown(shutting_down):
    fake = _raising_server(errno.ECONNRESET)

    with pytest.raises(OSError) as exc_info:
        server._run_server(fake)
    assert exc_info.value.errno == errno.ECONNRESET


def test_non_oserror_reraised_during_shutdown(shutting_down):
    fake = MagicMock()
    fake.run.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        server._run_server(fake)


def test_clean_run_returns_none(not_shutting_down):
    fake = MagicMock()

    assert server._run_server(fake) is None
    fake.run.assert_called_once_with()


def test_windows_winerror_10038_maps_to_suppressed_errno():
    """``[WinError 10038]`` must land in the suppressed set.

    On Windows, ``errno.ENOTSOCK`` is the WSA code 10038, so an
    ``OSError`` raised by ``select()`` for a closed socket carries
    that errno. On POSIX the constant differs but the guard still
    keys off ``errno.ENOTSOCK``.
    """
    assert errno.ENOTSOCK in server._SHUTDOWN_RACE_ERRNOS
    assert errno.EBADF in server._SHUTDOWN_RACE_ERRNOS
    if sys.platform == "win32":
        assert 10038 in server._SHUTDOWN_RACE_ERRNOS
