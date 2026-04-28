# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for ``launcher.wizard_orchestration`` (FR-L1, FR-L3, FR-L7a)."""

import argparse
from unittest.mock import MagicMock

import pytest

from launcher import wizard_ipc, wizard_orchestration


SECRET = b"a" * 32


@pytest.fixture(autouse=True)
def _mock_wizard_caddy(mocker):
    """Issue #170: stub the cert generator + Caddy supervisor."""
    mocker.patch(
        "launcher.wizard_caddy_lifecycle.generate_wizard_cert",
        return_value=(MagicMock(), MagicMock()),
    )
    mocker.patch(
        "launcher.wizard_caddy_wiring.start_wizard_caddy_supervisor",
        return_value=MagicMock(),
    )


class _FakeProc:
    def __init__(self, returncode=None, pid=12345):
        self.returncode = returncode
        self.pid = pid
        self.terminate_called = False
        self.kill_called = False

    def poll(self):
        return self.returncode

    def terminate(self):
        # ``terminate`` flips returncode to 0 so the launcher's grace
        # wait short-circuits without needing a real OS subprocess.
        self.terminate_called = True
        if self.returncode is None:
            self.returncode = 0

    def kill(self):
        self.kill_called = True
        if self.returncode is None:
            self.returncode = -9

    def wait(self, timeout=None):
        del timeout
        return self.returncode or 0


# ---- Token + secret generation -------------------------------------------

class TestSecretGeneration:

    def test_setup_token_is_url_safe_string(self):
        token = wizard_orchestration.generate_setup_token()
        assert isinstance(token, str)
        assert len(token) >= 32

    def test_setup_token_is_unique_per_call(self):
        a = wizard_orchestration.generate_setup_token()
        b = wizard_orchestration.generate_setup_token()
        assert a != b

    def test_ipc_secret_is_bytes(self):
        secret = wizard_orchestration.generate_ipc_secret()
        assert isinstance(secret, bytes)
        assert len(secret) >= 32

    def test_ipc_secret_is_unique_per_call(self):
        a = wizard_orchestration.generate_ipc_secret()
        b = wizard_orchestration.generate_ipc_secret()
        assert a != b


# ---- write_secret_files (FR-L3a / FR-L4a) --------------------------------

class TestWriteSecretFiles:

    def test_writes_both_files(self, tmp_path):
        wizard_orchestration.write_secret_files(
            tmp_path, "my-token", b"my-ipc-secret-bytes",
        )
        token_file = tmp_path / "wizard" / ".setup_token"
        secret_file = tmp_path / "wizard" / ".ipc_secret"
        assert token_file.exists()
        assert secret_file.exists()
        assert token_file.read_bytes() == b"my-token"
        assert secret_file.read_bytes() == b"my-ipc-secret-bytes"

    def test_writes_persistent_tray_token_copy(self, tmp_path):
        """Issue #172: tray needs a copy of the token that survives the
        wizard subprocess's consume-and-unlink of ``.setup_token``.

        The persistent ``setup_token`` (no leading dot) is identical
        content; same chmod-600 / Windows ACL hardening.
        """
        wizard_orchestration.write_secret_files(
            tmp_path, "my-token", b"my-ipc-secret-bytes",
        )
        tray_copy = tmp_path / "wizard" / "setup_token"
        dotted = tmp_path / "wizard" / ".setup_token"
        assert tray_copy.exists()
        assert tray_copy.read_bytes() == b"my-token"
        # The two files must hold identical content so they cannot
        # diverge as a source of confusion.
        assert tray_copy.read_bytes() == dotted.read_bytes()

    def test_creates_wizard_dir_if_missing(self, tmp_path):
        wizard_orchestration.write_secret_files(
            tmp_path, "x", b"y",
        )
        assert (tmp_path / "wizard").is_dir()


# ---- wait_for_wizard_done (FR-L7a) ---------------------------------------

class TestWaitForWizardDone:

    def test_returns_done_when_marker_observed(self, tmp_path):
        marker_path = tmp_path / "wizard" / wizard_ipc.MARKER_WIZARD_DONE
        wizard_ipc.write_marker(
            marker_path, "wizard_done", tmp_path, SECRET,
            payload={"topology": "manager"},
        )
        wizard_proc = _FakeProc()
        payload, reason = wizard_orchestration.wait_for_wizard_done(
            wizard_proc, tmp_path, SECRET, idle_timeout=2.0,
            poll_interval=0.05,
        )
        assert reason == "done"
        assert payload is not None
        assert payload["topology"] == "manager"
        # Marker must be deleted post-validation (FR-IPC3).
        assert not marker_path.exists()

    def test_returns_wizard_failed_on_nonzero_exit(self, tmp_path):
        wizard_proc = _FakeProc(returncode=2)
        payload, reason = wizard_orchestration.wait_for_wizard_done(
            wizard_proc, tmp_path, SECRET, idle_timeout=2.0,
            poll_interval=0.05,
        )
        assert reason == "wizard_failed"
        assert payload is None

    def test_returns_no_handshake_on_clean_exit_without_marker(
        self, tmp_path,
    ):
        wizard_proc = _FakeProc(returncode=0)
        payload, reason = wizard_orchestration.wait_for_wizard_done(
            wizard_proc, tmp_path, SECRET, idle_timeout=2.0,
            poll_interval=0.05,
        )
        assert reason == "wizard_no_handshake"
        assert payload is None

    def test_returns_idle_timeout(self, tmp_path):
        wizard_proc = _FakeProc()
        payload, reason = wizard_orchestration.wait_for_wizard_done(
            wizard_proc, tmp_path, SECRET, idle_timeout=0.1,
            poll_interval=0.02,
        )
        assert reason == "idle_timeout"
        assert payload is None


# ---- run_wizard_mode end-to-end (mocked) ---------------------------------

class TestRunWizardMode:
    """End-to-end happy-path with mocked subprocesses."""

    def _make_args(self, no_browser=True, print_url=True):
        return argparse.Namespace(
            no_browser=no_browser, print_url=print_url,
        )

    def test_happy_path_returns_zero(self, tmp_path, mocker):
        # Pre-write topology.json so the runtime hand-off succeeds.
        (tmp_path / "topology.json").write_text(
            '{"topology": "worker_only"}', encoding="utf-8",
        )
        # Wizard writes its port file and the .wizard_done marker.
        (tmp_path / "wizard").mkdir()
        (tmp_path / "wizard" / "loopback_port").write_text("8101")

        wizard_proc = _FakeProc()
        runtime_proc = _FakeProc()

        def _start(component, **kwargs):
            del kwargs
            if component == "wizard":
                # Simulate wizard's marker write (uses real ipc_secret).
                # Find the secret file; read+write marker so the launcher
                # observes it on the next poll iteration.
                secret = (
                    tmp_path / "wizard" / ".ipc_secret"
                ).read_bytes()
                wizard_ipc.write_marker(
                    tmp_path / "wizard" / wizard_ipc.MARKER_WIZARD_DONE,
                    "wizard_done", tmp_path, secret,
                    payload={"topology": "worker_only"},
                )
                return wizard_proc
            return runtime_proc

        # FR-7: wizard health probe — no real server in unit tests, mock True.
        mocker.patch(
            "launcher.wizard_orchestration.wait_for_health",
            return_value=True,
        )
        mocker.patch(
            "launcher.wizard_runtime.wait_for_runtime_port_bind",
            return_value=True,
        )

        rc = wizard_orchestration.run_wizard_mode(
            tmp_path, self._make_args(),
            bootstrap_first_run=MagicMock(),
            start_component=_start,
            idle_timeout=5.0,
        )
        assert rc == 0

    def test_idle_timeout_exits_nonzero(self, tmp_path, mocker):
        wizard_proc = _FakeProc()
        # Pre-create wizard dir + port file so URL surface doesn't hang.
        (tmp_path / "wizard").mkdir()
        (tmp_path / "wizard" / "loopback_port").write_text("8100")

        terminate = mocker.patch(
            "launcher.wizard_runtime.terminate_wizard",
        )
        mocker.patch(
            "launcher.wizard_orchestration.surface_wizard_url",
        )
        mocker.patch(
            "launcher.wizard_orchestration.wait_for_health",
            return_value=True,
        )

        rc = wizard_orchestration.run_wizard_mode(
            tmp_path, self._make_args(),
            bootstrap_first_run=MagicMock(),
            start_component=MagicMock(return_value=wizard_proc),
            idle_timeout=0.1,
        )
        assert rc == 1
        terminate.assert_called_once()

    def test_missing_port_file_aborts_handoff(self, tmp_path, mocker):
        """MED-4 (Phase F1): no silent fallback to 8100 when the wizard
        never writes its port file — that produced wrong banner URLs."""
        # No port file written.
        wizard_proc = _FakeProc()
        terminate = mocker.patch(
            "launcher.wizard_runtime.terminate_wizard",
        )
        # Speed up the inner wait by patching the timeout floor.
        mocker.patch(
            "launcher.wizard_orchestration._wait_for_wizard_port",
            return_value=None,
        )
        rc = wizard_orchestration.run_wizard_mode(
            tmp_path, self._make_args(),
            bootstrap_first_run=MagicMock(),
            start_component=MagicMock(return_value=wizard_proc),
            idle_timeout=2.0,
        )
        assert rc == 1, (
            "missing port file MUST abort, not fall back to 8100"
        )
        terminate.assert_called_once()

    def test_wizard_failure_exits_nonzero(self, tmp_path, mocker):
        wizard_proc = _FakeProc(returncode=2)
        (tmp_path / "wizard").mkdir()
        (tmp_path / "wizard" / "loopback_port").write_text("8100")
        mocker.patch(
            "launcher.wizard_orchestration.surface_wizard_url",
        )
        # Wizard exits 2 — wait_for_health detects the dead proc and
        # returns False; FR-11(c) routes through wizard_health_timeout.
        mocker.patch(
            "launcher.wizard_orchestration.wait_for_health",
            return_value=False,
        )

        rc = wizard_orchestration.run_wizard_mode(
            tmp_path, self._make_args(),
            bootstrap_first_run=MagicMock(),
            start_component=MagicMock(return_value=wizard_proc),
            idle_timeout=2.0,
        )
        assert rc == 1


# Run-launcher routing tests live in
# ``test_wizard_orchestration_routing.py`` (split out for the 300-line
# ceiling).
