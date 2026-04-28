# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Issue #170 sequencing tests for ``launcher.wizard_orchestration``.

Covers the three new ACs added by the wizard Caddy consolidation:

* AC-CertGeneratedByLauncher / AC-CaddyStartsBeforeWizard — cert
  before wizard spawn, wizard before Caddy start.
* AC-WizardPortFileIsCaddyPort — ``<data_dir>/wizard/port`` carries
  Caddy's public TLS port (8100), not the wizard's loopback port.
* AC-CleanupTerminatesCaddy — wizard subprocess termination precedes
  Caddy supervisor teardown.

Split from ``test_wizard_orchestration.py`` so that file stays under
the 300-line ceiling (CLAUDE.md).
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import pytest

from launcher import wizard_ipc, wizard_orchestration


class _FakeProc:
    def __init__(self, returncode=None, pid=12345):
        self.returncode = returncode
        self.pid = pid

    def poll(self):
        return self.returncode

    def terminate(self):
        if self.returncode is None:
            self.returncode = 0

    def kill(self):
        if self.returncode is None:
            self.returncode = -9

    def wait(self, timeout=None):
        del timeout
        return self.returncode or 0


def _args_ns():
    return argparse.Namespace(no_browser=True, print_url=True)


def _setup_topology(tmp_path):
    (tmp_path / "topology.json").write_text(
        '{"topology": "worker_only"}', encoding="utf-8",
    )
    (tmp_path / "wizard").mkdir()
    (tmp_path / "wizard" / "loopback_port").write_text("8101")


def _make_start_component(tmp_path, wizard_proc, runtime_proc, log=None):
    """Return a ``start_component`` stub matching the launcher contract."""
    def _start(component, **kwargs):
        del kwargs
        if log is not None:
            log.append(f"spawn:{component}")
        if component == "wizard":
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
    return _start


@pytest.fixture
def common_mocks(mocker):
    """Patch wait_for_health + runtime port-bind to True for happy path."""
    mocker.patch(
        "launcher.wizard_orchestration.wait_for_health",
        return_value=True,
    )
    mocker.patch(
        "launcher.wizard_runtime.wait_for_runtime_port_bind",
        return_value=True,
    )


# ---------------------------------------------------------------------
# AC-CertGeneratedByLauncher / AC-CaddyStartsBeforeWizard
# ---------------------------------------------------------------------

def test_cert_generated_before_wizard_spawn(
    tmp_path, mocker, common_mocks,
):
    """Sequencing: cert -> spawn:wizard -> caddy."""
    _setup_topology(tmp_path)
    call_order: list[str] = []

    def _record_cert(*a, **kw):
        call_order.append("cert")
        return (MagicMock(), MagicMock())

    def _record_caddy(*a, **kw):
        call_order.append("caddy")
        return MagicMock()

    mocker.patch(
        "launcher.wizard_caddy_lifecycle.generate_wizard_cert",
        side_effect=_record_cert,
    )
    mocker.patch(
        "launcher.wizard_caddy_wiring.start_wizard_caddy_supervisor",
        side_effect=_record_caddy,
    )

    rc = wizard_orchestration.run_wizard_mode(
        tmp_path, _args_ns(),
        bootstrap_first_run=MagicMock(),
        start_component=_make_start_component(
            tmp_path, _FakeProc(), _FakeProc(), log=call_order,
        ),
        idle_timeout=5.0,
    )
    assert rc == 0
    # cert MUST happen before the wizard spawns; Caddy MUST start
    # AFTER the wizard binds its loopback port.
    assert call_order.index("cert") < call_order.index("spawn:wizard")
    assert call_order.index("spawn:wizard") < call_order.index("caddy")


# ---------------------------------------------------------------------
# AC-WizardPortFileIsCaddyPort
# ---------------------------------------------------------------------

def test_caddy_port_written_to_port_file(
    tmp_path, mocker, common_mocks,
):
    """``write_wizard_public_port_file`` is called with Caddy's port (8100)."""
    from launcher import wizard_caddy_wiring
    _setup_topology(tmp_path)

    mocker.patch(
        "launcher.wizard_caddy_lifecycle.generate_wizard_cert",
        return_value=(MagicMock(), MagicMock()),
    )
    mocker.patch(
        "launcher.wizard_caddy_wiring.start_wizard_caddy_supervisor",
        return_value=MagicMock(),
    )
    write_port = mocker.patch(
        "launcher.wizard_caddy_lifecycle.write_wizard_public_port_file",
    )

    wizard_orchestration.run_wizard_mode(
        tmp_path, _args_ns(),
        bootstrap_first_run=MagicMock(),
        start_component=_make_start_component(
            tmp_path, _FakeProc(), _FakeProc(),
        ),
        idle_timeout=5.0,
    )
    write_port.assert_called_once()
    called_data_dir, called_port = write_port.call_args.args
    assert called_data_dir == tmp_path
    assert called_port == wizard_caddy_wiring.WIZARD_PUBLIC_TLS_PORT


# ---------------------------------------------------------------------
# AC-CleanupTerminatesCaddy
# ---------------------------------------------------------------------

def test_cleanup_stops_caddy_after_wizard_on_done(
    tmp_path, mocker, common_mocks,
):
    """Wizard subprocess terminates BEFORE Caddy supervisor stop."""
    _setup_topology(tmp_path)

    mocker.patch(
        "launcher.wizard_caddy_lifecycle.generate_wizard_cert",
        return_value=(MagicMock(), MagicMock()),
    )
    fake_supervisor = MagicMock()
    mocker.patch(
        "launcher.wizard_caddy_wiring.start_wizard_caddy_supervisor",
        return_value=fake_supervisor,
    )

    teardown_order: list[str] = []
    original_terminate = wizard_orchestration.wizard_runtime.terminate_wizard

    def _record_term(proc):
        teardown_order.append("wizard")
        return original_terminate(proc)

    mocker.patch(
        "launcher.wizard_runtime.terminate_wizard",
        side_effect=_record_term,
    )
    fake_supervisor.stop.side_effect = lambda timeout=5.0: (
        teardown_order.append("caddy")
    )

    wizard_orchestration.run_wizard_mode(
        tmp_path, _args_ns(),
        bootstrap_first_run=MagicMock(),
        start_component=_make_start_component(
            tmp_path, _FakeProc(), _FakeProc(),
        ),
        idle_timeout=5.0,
    )
    assert "wizard" in teardown_order
    assert "caddy" in teardown_order
    assert teardown_order.index("wizard") < teardown_order.index("caddy")
