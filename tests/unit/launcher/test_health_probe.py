# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``launcher.health_probe`` (v2 splash phase states spec).

Covers:

* :func:`wait_for_health` returns True on 200 + FR-W14 envelope.
* :func:`wait_for_health` returns False on timeout, Popen exit, KI,
  bad envelope, non-JSON.
* :func:`wait_for_health` rejects non-loopback URLs (NFR-6).
* :func:`wait_for_health` returns False within 1 s when ``proc`` exits
  before health (FR-7 — wizard-crash-fast-fail).
* Manager+worker serial polling shared-deadline arithmetic (FR-6).
* :func:`probe_health_once` envelope-validation matrix.
"""

from __future__ import annotations

import json

import pytest

from launcher import health_probe
from launcher.health_probe import (
    NonLoopbackHealthURL,
    probe_health_once,
    wait_for_health,
)


# ---------------------------------------------------------------------
# probe_health_once — envelope validation
# ---------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body

    def getcode(self):
        return self.status


def _stub_urlopen(mocker, *, status=200, body=None):
    if body is None:
        body = json.dumps(
            {"boot_id": "abc", "version": "1.0"},
        ).encode("utf-8")
    return mocker.patch.object(
        health_probe.urllib.request, "urlopen",
        return_value=_FakeResp(status, body),
    )


class TestProbeHealthOnce:

    URL = "https://127.0.0.1:8080/api/health/"

    def test_returns_true_on_200_with_full_envelope(self, mocker):
        _stub_urlopen(mocker)
        assert probe_health_once(self.URL) is True

    def test_returns_false_on_non_200(self, mocker):
        _stub_urlopen(mocker, status=503)
        assert probe_health_once(self.URL) is False

    def test_returns_false_on_envelope_missing_boot_id(self, mocker):
        _stub_urlopen(
            mocker,
            body=json.dumps({"version": "1.0"}).encode("utf-8"),
        )
        assert probe_health_once(self.URL) is False

    def test_returns_false_on_envelope_missing_version(self, mocker):
        _stub_urlopen(
            mocker,
            body=json.dumps({"boot_id": "x"}).encode("utf-8"),
        )
        assert probe_health_once(self.URL) is False

    def test_returns_false_on_non_json_body(self, mocker):
        _stub_urlopen(mocker, body=b"not json")
        assert probe_health_once(self.URL) is False

    def test_returns_false_on_non_dict_body(self, mocker):
        _stub_urlopen(mocker, body=b"[]")
        assert probe_health_once(self.URL) is False

    def test_returns_false_on_connection_refused(self, mocker):
        mocker.patch.object(
            health_probe.urllib.request, "urlopen",
            side_effect=ConnectionRefusedError(),
        )
        assert probe_health_once(self.URL) is False


# ---------------------------------------------------------------------
# wait_for_health — loopback gate (NFR-6 / AC-LoopbackOnly)
# ---------------------------------------------------------------------

class TestLoopbackGate:

    def test_accepts_127_0_0_1(self, mocker):
        _stub_urlopen(mocker)
        assert wait_for_health(
            "https://127.0.0.1:8080/api/health/",
        ) is True

    def test_accepts_ipv6_loopback(self, mocker):
        _stub_urlopen(mocker)
        assert wait_for_health(
            "https://[::1]:8080/api/health/",
        ) is True

    def test_accepts_localhost_literal(self, mocker):
        _stub_urlopen(mocker)
        assert wait_for_health(
            "https://localhost:8080/api/health/",
        ) is True

    def test_rejects_remote_hostname_before_any_request(self, mocker):
        urlopen = mocker.patch.object(
            health_probe.urllib.request, "urlopen",
        )
        with pytest.raises(NonLoopbackHealthURL):
            wait_for_health("https://example.com/api/health/")
        urlopen.assert_not_called()

    def test_rejects_lan_ip(self, mocker):
        urlopen = mocker.patch.object(
            health_probe.urllib.request, "urlopen",
        )
        with pytest.raises(NonLoopbackHealthURL):
            wait_for_health("https://192.168.1.10/api/health/")
        urlopen.assert_not_called()

    def test_rejects_public_ip(self):
        with pytest.raises(NonLoopbackHealthURL):
            wait_for_health("https://8.8.8.8/api/health/")


# ---------------------------------------------------------------------
# wait_for_health — outcomes
# ---------------------------------------------------------------------

URL = "https://127.0.0.1:8080/api/health/"


class TestWaitForHealthSuccess:

    def test_returns_true_on_immediate_200(self, mocker):
        _stub_urlopen(mocker)
        assert wait_for_health(URL) is True


class TestWaitForHealthTimeout:

    def test_returns_false_on_wall_clock_timeout(self, mocker):
        # urlopen always raises ConnectionRefused so probe stays False.
        mocker.patch.object(
            health_probe.urllib.request, "urlopen",
            side_effect=ConnectionRefusedError(),
        )
        # Drive monotonic so the deadline trips on the second check.
        # Sequence: deadline computation, first loop check, second.
        mocker.patch.object(
            health_probe.time, "monotonic",
            side_effect=[0.0, 0.1, 31.0],
        )
        mocker.patch.object(health_probe.time, "sleep")
        assert wait_for_health(URL, timeout=30.0) is False

    def test_returns_false_on_non_positive_timeout(self):
        # FR-6 shared-deadline arithmetic: when manager_proc has burned
        # the entire 30 s budget, the worker call gets timeout <= 0.
        # Don't issue a network call; treat as immediate timeout.
        assert wait_for_health(URL, timeout=0.0) is False
        assert wait_for_health(URL, timeout=-0.5) is False


class TestWaitForHealthProcExit:

    def test_returns_false_when_proc_exits_before_health(
        self, mocker,
    ):
        proc = mocker.MagicMock()
        proc.poll.return_value = 1
        proc.returncode = 1
        urlopen = mocker.patch.object(
            health_probe.urllib.request, "urlopen",
        )
        assert wait_for_health(URL, proc=proc) is False
        urlopen.assert_not_called()

    def test_wizard_crash_after_port_file_fast_fails_within_1s(
        self, mocker,
    ):
        """FR-7 / AC-WizardCrashFastFail.

        Wizard writes its port file then crashes before serving HTTP.
        wait_for_health(wizard_url, wizard_proc) must return False
        within 1 s (≤ 4 poll intervals at 250 ms cadence) instead of
        burning the full 30 s budget on a dead subprocess.
        """
        proc = mocker.MagicMock()
        # Alive on first probe iteration (dead on second — wizard
        # crashed after the port-file write was observed).
        proc.poll.side_effect = [None, 1, 1]
        proc.returncode = 1
        # Probe always returns False (HTTP not yet up).
        mocker.patch.object(
            health_probe, "probe_health_once", return_value=False,
        )
        sleeps = []
        mocker.patch.object(
            health_probe.time, "sleep",
            side_effect=lambda s: sleeps.append(s),
        )
        # Fresh deadline computed at entry (no monotonic mocking needed
        # — the proc-exit branch trips before any deadline check).
        assert wait_for_health(URL, proc=proc, timeout=30.0) is False
        # Verify ≤ 4 poll intervals — proves the dead-proc branch fires
        # within ~1 s rather than spinning the full 30 s.
        assert len(sleeps) <= 4


class TestWaitForHealthKeyboardInterrupt:

    def test_returns_false_on_ki_during_probe(self, mocker):
        """FR-3 / AC-KIEnvelope: KI inside probe MUST return False, not
        propagate to the caller."""
        mocker.patch.object(
            health_probe.urllib.request, "urlopen",
            side_effect=KeyboardInterrupt(),
        )
        # Guard: if the KI envelope leaks, this assertion never runs.
        result = wait_for_health(URL)
        assert result is False

    def test_returns_false_on_ki_during_sleep(self, mocker):
        mocker.patch.object(
            health_probe.urllib.request, "urlopen",
            side_effect=ConnectionRefusedError(),
        )
        mocker.patch.object(
            health_probe.time, "sleep",
            side_effect=KeyboardInterrupt(),
        )
        result = wait_for_health(URL)
        assert result is False
