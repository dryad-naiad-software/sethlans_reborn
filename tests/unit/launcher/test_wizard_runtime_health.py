# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for ``launcher.wizard_runtime`` — port-bind + health-probe facets.

Covers FR-L7 / FR-L7b. The topology-spawn / handoff / termination
facets live in ``test_wizard_runtime_handoff.py`` so each file stays
under the 300-line limit.
"""

import json

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

    def _stub_urlopen(self, mocker, *, status=200, body=None):
        """Patch urllib.request.urlopen to return a fake response."""

        class _Resp:
            def __init__(self, st, payload):
                self.status = st
                self._payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return self._payload

            def getcode(self):
                return self.status

        if body is None:
            body = json.dumps({
                "boot_id": "abc", "version": "1.0",
            }).encode("utf-8")
        return mocker.patch(
            "launcher.wizard_runtime.urllib.request.urlopen",
            return_value=_Resp(status, body),
        )

    def test_returns_true_on_200_with_envelope(self, mocker):
        self._stub_urlopen(mocker)
        assert wizard_runtime._probe_runtime_health(8080) is True

    def test_returns_false_on_non_200(self, mocker):
        self._stub_urlopen(mocker, status=503)
        assert wizard_runtime._probe_runtime_health(8080) is False

    def test_returns_false_on_missing_keys(self, mocker):
        self._stub_urlopen(
            mocker, body=json.dumps({"boot_id": "x"}).encode("utf-8"),
        )
        assert wizard_runtime._probe_runtime_health(8080) is False

    def test_returns_false_on_non_json(self, mocker):
        self._stub_urlopen(mocker, body=b"not json")
        assert wizard_runtime._probe_runtime_health(8080) is False

    def test_returns_false_on_connection_refused(self, mocker):
        mocker.patch(
            "launcher.wizard_runtime.urllib.request.urlopen",
            side_effect=ConnectionRefusedError(),
        )
        assert wizard_runtime._probe_runtime_health(8080) is False


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
