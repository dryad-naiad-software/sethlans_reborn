# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``launcher.wizard_runtime`` Caddy wiring (#202).

Covers:

* FR-CADDY1/2: ``hand_off_to_runtime`` starts the manager Caddy supervisor
  via ``prepare_manager_caddy_and_resolve_port`` for manager-bearing
  topologies, BEFORE ``wait_for_runtime_port_bind`` is called.
* FR-CADDY1 (negative): worker-only topologies do NOT start the
  manager Caddy supervisor.
* FR-PROBE1/3: the port passed to ``wait_for_runtime_port_bind`` is the
  value returned by ``prepare_manager_caddy_and_resolve_port`` (which
  resolves it from ``manager.ini``), not the module-level constant
  ``RUNTIME_MANAGER_PORT``.
"""

from __future__ import annotations

import json

import pytest

from launcher import wizard_runtime

from ._wizard_runtime_helpers import SECRET, FakeProc


def _write_topology(data_dir, topology):
    (data_dir / "topology.json").write_text(
        json.dumps({"topology": topology}), encoding="utf-8",
    )


class TestHandOffStartsManagerCaddy:
    """FR-CADDY1/2: manager Caddy is started before port-bind probe."""

    def test_manager_worker_topology_invokes_prepare_helper(
        self, tmp_path, mocker,
    ):
        _write_topology(tmp_path, "manager_worker")
        mocker.patch(
            "launcher.apply_pending_setup.run_apply_pipeline_if_needed",
            return_value=None,
        )
        prepare = mocker.patch(
            "launcher.caddy_wiring.prepare_manager_caddy_and_resolve_port",
            return_value=9090,
        )
        wait = mocker.patch(
            "launcher.wizard_runtime.wait_for_runtime_port_bind",
            return_value=True,
        )
        mocker.patch(
            "launcher.wizard_runtime.wizard_dir.cleanup_wizard_dir",
        )

        rc = wizard_runtime.hand_off_to_runtime(
            payload={"topology": "manager_worker"},
            data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=FakeProc(),
            bootstrap_first_run=mocker.MagicMock(),
            start_component=mocker.MagicMock(return_value=FakeProc()),
        )

        assert rc == 0
        prepare.assert_called_once_with(tmp_path / "manager")
        wait.assert_called_once()
        # The port forwarded to wait_for_runtime_port_bind must come
        # from prepare (i.e. 9090), NOT the module constant 8080.
        assert wait.call_args.args[1] == 9090
        assert wait.call_args.args[1] != wizard_runtime.RUNTIME_MANAGER_PORT

    def test_manager_topology_invokes_prepare_helper(
        self, tmp_path, mocker,
    ):
        _write_topology(tmp_path, "manager")
        mocker.patch(
            "launcher.apply_pending_setup.run_apply_pipeline_if_needed",
            return_value=None,
        )
        prepare = mocker.patch(
            "launcher.caddy_wiring.prepare_manager_caddy_and_resolve_port",
            return_value=8080,
        )
        mocker.patch(
            "launcher.wizard_runtime.wait_for_runtime_port_bind",
            return_value=True,
        )
        mocker.patch(
            "launcher.wizard_runtime.wizard_dir.cleanup_wizard_dir",
        )

        wizard_runtime.hand_off_to_runtime(
            payload={"topology": "manager"},
            data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=FakeProc(),
            bootstrap_first_run=mocker.MagicMock(),
            start_component=mocker.MagicMock(return_value=FakeProc()),
        )

        prepare.assert_called_once_with(tmp_path / "manager")

    def test_worker_only_topology_does_not_invoke_prepare(
        self, tmp_path, mocker,
    ):
        """FR-CADDY1 negative + AC-5: worker-only never starts manager Caddy."""
        _write_topology(tmp_path, "worker_only")
        mocker.patch(
            "launcher.apply_pending_setup.run_apply_pipeline_if_needed",
            return_value=None,
        )
        prepare = mocker.patch(
            "launcher.caddy_wiring.prepare_manager_caddy_and_resolve_port",
        )
        wait = mocker.patch(
            "launcher.wizard_runtime.wait_for_runtime_port_bind",
        )
        mocker.patch(
            "launcher.wizard_runtime.wizard_dir.cleanup_wizard_dir",
        )

        rc = wizard_runtime.hand_off_to_runtime(
            payload={"topology": "worker_only"},
            data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=FakeProc(),
            bootstrap_first_run=mocker.MagicMock(),
            start_component=mocker.MagicMock(return_value=FakeProc()),
        )

        assert rc == 0
        prepare.assert_not_called()
        wait.assert_not_called()

    def test_prepare_runs_before_wait_for_port_bind(self, tmp_path, mocker):
        """FR-CADDY1 ordering: Caddy is up before the launcher probes."""
        _write_topology(tmp_path, "manager_worker")
        mocker.patch(
            "launcher.apply_pending_setup.run_apply_pipeline_if_needed",
            return_value=None,
        )
        order: list[str] = []
        mocker.patch(
            "launcher.caddy_wiring.prepare_manager_caddy_and_resolve_port",
            side_effect=lambda *_a, **_kw: order.append("prepare") or 8080,
        )
        mocker.patch(
            "launcher.wizard_runtime.wait_for_runtime_port_bind",
            side_effect=lambda *_a, **_kw: order.append("wait") or True,
        )
        mocker.patch(
            "launcher.wizard_runtime.wizard_dir.cleanup_wizard_dir",
        )

        wizard_runtime.hand_off_to_runtime(
            payload={"topology": "manager_worker"},
            data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=FakeProc(),
            bootstrap_first_run=mocker.MagicMock(),
            start_component=mocker.MagicMock(return_value=FakeProc()),
        )

        assert order == ["prepare", "wait"], (
            "prepare_manager_caddy_and_resolve_port MUST run BEFORE "
            "wait_for_runtime_port_bind (FR-CADDY1 ordering)"
        )

    def test_prepare_helper_failure_propagates(self, tmp_path, mocker):
        """FR-LIFECYCLE3: a Caddy start failure propagates out (unusable)."""
        _write_topology(tmp_path, "manager")
        mocker.patch(
            "launcher.apply_pending_setup.run_apply_pipeline_if_needed",
            return_value=None,
        )
        mocker.patch(
            "launcher.caddy_wiring.prepare_manager_caddy_and_resolve_port",
            side_effect=RuntimeError("caddy binary missing"),
        )

        with pytest.raises(RuntimeError, match="caddy binary missing"):
            wizard_runtime.hand_off_to_runtime(
                payload={"topology": "manager"},
                data_dir=tmp_path, ipc_secret=SECRET,
                wizard_proc=FakeProc(),
                bootstrap_first_run=mocker.MagicMock(),
                start_component=mocker.MagicMock(return_value=FakeProc()),
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
