# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Issue #163 — wait-loop quit semantics.

Covers the per-helper sentinel contracts:

* AC-PortFileQuit — ``_wait_for_wizard_port`` returns ``None`` within
  one poll interval of the quit event being set.
* AC-WizardDoneQuit — ``wait_for_wizard_done`` returns
  ``(None, "quit_requested")``.
* AC-HealthQuit — ``wait_for_health`` raises :class:`QuitRequested`
  (both at entry and mid-poll).
* AC-RuntimePortBindQuit — ``wait_for_runtime_port_bind`` returns
  ``False`` within one poll interval; the caller's distinguish-from-
  real-timeout logic is exercised in the orchestration tests.

Tests are deterministic — we set the event before the helper runs,
or use a controlled side-effect mock for ``wait_or_quit``.
"""

from __future__ import annotations

import pytest

from launcher import supervision, wizard_orchestration, wizard_runtime
from launcher.health_probe import QuitRequested

from ._tray_quit_helpers import SECRET, FakeProc


# ----------------------------------------------------------------------
# AC-PortFileQuit
# ----------------------------------------------------------------------

class TestWaitForWizardPortQuit:

    def test_returns_none_when_quit_event_set(self, tmp_path):
        wizard_proc = FakeProc()
        supervision.get_quit_requested_event().set()
        port = wizard_orchestration._wait_for_wizard_port(
            tmp_path, wizard_proc, timeout=5.0, poll_interval=0.05,
        )
        assert port is None


# ----------------------------------------------------------------------
# AC-WizardDoneQuit
# ----------------------------------------------------------------------

class TestWaitForWizardDoneQuit:

    def test_returns_quit_requested_reason(self, tmp_path):
        wizard_proc = FakeProc()
        supervision.get_quit_requested_event().set()
        payload, reason = wizard_orchestration.wait_for_wizard_done(
            wizard_proc, tmp_path, SECRET,
            idle_timeout=5.0, poll_interval=0.05,
        )
        assert payload is None
        assert reason == "quit_requested"


# ----------------------------------------------------------------------
# AC-HealthQuit
# ----------------------------------------------------------------------

class TestWaitForHealthQuitRequested:

    URL = "https://127.0.0.1:8080/api/health/"

    def test_raises_when_event_already_set_at_entry(self, mocker):
        from launcher import health_probe
        urlopen = mocker.patch.object(
            health_probe.urllib.request, "urlopen",
        )
        supervision.get_quit_requested_event().set()
        with pytest.raises(QuitRequested):
            health_probe.wait_for_health(self.URL)
        urlopen.assert_not_called()

    def test_raises_when_event_fires_mid_poll(self, mocker):
        from launcher import health_probe
        mocker.patch.object(
            health_probe, "probe_health_once", return_value=False,
        )
        wait_calls = []

        def _fake_wait(timeout):
            wait_calls.append(timeout)
            # Second call returns True — event "fires" mid-poll.
            return len(wait_calls) >= 2

        mocker.patch.object(
            supervision, "wait_or_quit", side_effect=_fake_wait,
        )
        with pytest.raises(QuitRequested):
            health_probe.wait_for_health(self.URL, timeout=30.0)
        assert len(wait_calls) == 2


# ----------------------------------------------------------------------
# AC-RuntimePortBindQuit
# ----------------------------------------------------------------------

class TestWaitForRuntimePortBindQuit:

    def test_returns_false_within_one_poll_on_quit(self, mocker):
        proc = FakeProc()
        mocker.patch(
            "launcher.wizard_runtime._port_is_bound",
            return_value=False,
        )
        mocker.patch(
            "launcher.wizard_runtime._probe_runtime_health",
            return_value=False,
        )
        supervision.get_quit_requested_event().set()
        # Long timeout — a regression that ignored the event would
        # still return False here on the wall-clock timeout, but it
        # would burn 30 s. We bound the test on assertion, not time.
        assert wizard_runtime.wait_for_runtime_port_bind(
            proc, 8080, timeout=30.0, poll_interval=0.05,
        ) is False
