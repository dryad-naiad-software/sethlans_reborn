# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for ``launcher.wizard_runtime`` — port-bind + health-probe facets.

Covers FR-L7 / FR-L7b. The topology-spawn / handoff / termination
facets live in ``test_wizard_runtime_handoff.py`` so each file stays
under the 300-line limit.
"""

import pytest

from launcher import wizard_runtime

from ._wizard_runtime_helpers import FakeProc


# ---- wait_for_runtime_port_bind -------------------------------------------

class TestWaitForRuntimePortBind:

    def test_returns_true_when_port_binds_and_health_ok(self, mocker):
        proc = FakeProc()
        mocker.patch(
            "launcher.wizard_runtime._port_is_bound", return_value=True,
        )
        # HIGH-1 (Phase F1): port-bind alone is no longer sufficient;
        # the health probe must also succeed.
        mocker.patch(
            "launcher.wizard_runtime._probe_runtime_health",
            return_value=True,
        )
        assert wizard_runtime.wait_for_runtime_port_bind(
            proc, 8080, timeout=1.0,
        ) is True

    def test_returns_false_when_runtime_exits_nonzero(self, mocker):
        proc = FakeProc(returncode=1)
        mocker.patch(
            "launcher.wizard_runtime._port_is_bound", return_value=False,
        )
        mocker.patch(
            "launcher.wizard_runtime._probe_runtime_health",
            return_value=False,
        )
        assert wizard_runtime.wait_for_runtime_port_bind(
            proc, 8080, timeout=1.0,
        ) is False

    def test_returns_false_on_timeout(self, mocker):
        proc = FakeProc()
        mocker.patch(
            "launcher.wizard_runtime._port_is_bound", return_value=False,
        )
        mocker.patch(
            "launcher.wizard_runtime._probe_runtime_health",
            return_value=False,
        )
        assert wizard_runtime.wait_for_runtime_port_bind(
            proc, 8080, timeout=0.05, poll_interval=0.01,
        ) is False

    def test_port_bound_but_health_fails_returns_false(self, mocker):
        # HIGH-1 regression — wizard would previously hand off to a
        # runtime that bound its socket but is still loading.
        proc = FakeProc()
        mocker.patch(
            "launcher.wizard_runtime._port_is_bound", return_value=True,
        )
        mocker.patch(
            "launcher.wizard_runtime._probe_runtime_health",
            return_value=False,
        )
        assert wizard_runtime.wait_for_runtime_port_bind(
            proc, 8080, timeout=0.2, poll_interval=0.05,
        ) is False


# ---- _probe_runtime_health (HIGH-1, Phase F1) -----------------------------

class TestProbeRuntimeHealth:
    """``_probe_runtime_health`` delegates to
    :func:`launcher.health_probe.probe_health_once` (OQ-2 consolidation,
    v2 splash phase states spec). The envelope-validation contract is
    therefore tested in ``test_health_probe.py``; here we only verify
    the wizard_runtime wrapper builds the expected URL and forwards
    the result.
    """

    def _stub_probe(self, mocker, return_value=True):
        return mocker.patch(
            "launcher.wizard_runtime.probe_health_once",
            return_value=return_value,
        )

    def test_returns_true_when_probe_returns_true(self, mocker):
        spy = self._stub_probe(mocker, return_value=True)
        assert wizard_runtime._probe_runtime_health(8080) is True
        spy.assert_called_once()
        url = spy.call_args.args[0]
        assert url == "https://127.0.0.1:8080/api/health/"

    def test_returns_false_when_probe_returns_false(self, mocker):
        self._stub_probe(mocker, return_value=False)
        assert wizard_runtime._probe_runtime_health(8080) is False

    def test_uses_custom_host(self, mocker):
        spy = self._stub_probe(mocker, return_value=True)
        wizard_runtime._probe_runtime_health(8080, host="127.0.0.2")
        url = spy.call_args.args[0]
        assert url == "https://127.0.0.2:8080/api/health/"


# ---- wizard_failure_exit --------------------------------------------------

class TestWizardFailureExit:

    def test_returns_nonzero(self):
        assert wizard_runtime.wizard_failure_exit("any") == 1

    def test_does_not_raise(self):
        # Sanity: the function is purely log + return, never raises.
        for reason in (
            "wizard_failed", "wizard_no_handshake",
            "idle_timeout", "topology_missing",
            "runtime_spawn_failed", "port_bind_timeout",
        ):
            assert wizard_runtime.wizard_failure_exit(reason) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
