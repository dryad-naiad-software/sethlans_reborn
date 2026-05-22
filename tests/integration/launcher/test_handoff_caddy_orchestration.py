# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Integration test: full ``hand_off_to_runtime`` flow with Caddy wiring (#202).

FR-TESTS4 — exercise the post-handoff path end-to-end for a manager
topology. Mocks only the subprocess layer (so no real Django process,
no real Caddy binary). The apply pipeline is the REAL orchestrator,
never monkey-patched (no repeat of the ``47d0575`` anti-pattern called
out in spec FR-TESTS4).

What we assert:

* ``start_caddy_supervisor`` is invoked with ``data_dir / "manager"``.
* ``wait_for_runtime_port_bind`` receives the port returned by
  ``_resolve_caddy_public_port`` (manager.ini's ``[server] port``).
* The real apply pipeline orchestrator runs (we see the migrate + apply
  subprocess argv shapes, and the ``--data-dir`` is the verbatim
  ``data_dir`` per #195 regression guard).
"""

from __future__ import annotations

import json

import pytest

from launcher import apply_pending_setup as apply_mod
from launcher import wizard_runtime

from tests.integration.launcher._dispatch_helpers import (
    MANAGER_DIR,
    _curated_env_for_subprocess,
    _write_pending,
)
from tests.unit.launcher._wizard_runtime_helpers import SECRET, FakeProc


def _write_topology(data_dir, topology="manager_worker"):
    (data_dir / "topology.json").write_text(
        json.dumps({"topology": topology}), encoding="utf-8",
    )


def _write_manager_ini(data_dir, port=8085):
    """Write a manager.ini under ``<data_dir>/manager/`` with a custom port."""
    manager_dir = data_dir / "manager"
    manager_dir.mkdir(parents=True, exist_ok=True)
    (manager_dir / "manager.ini").write_text(
        f"[server]\nport = {port}\n", encoding="utf-8",
    )


class TestHandOffCaddyOrchestration:
    """Full ``hand_off_to_runtime`` orchestration with real apply pipeline."""

    def _stub_subprocess(self, mocker):
        """Patch the apply pipeline's subprocess layer (capture argv)."""
        mocker.patch.object(apply_mod, "_is_frozen", return_value=False)
        mocker.patch.object(apply_mod, "_manager_dir", return_value=MANAGER_DIR)
        mocker.patch.object(
            apply_mod, "build_curated_env",
            return_value=_curated_env_for_subprocess(),
        )
        completed_ok = mocker.MagicMock(returncode=0, stdout="", stderr="")
        return mocker.patch.object(
            apply_mod.subprocess, "run", return_value=completed_ok,
        )

    def test_full_handoff_starts_caddy_with_manager_data(
        self, tmp_path, mocker,
    ):
        """FR-TESTS4 (a): ``start_caddy_supervisor`` called with the manager
        data dir; FR-TESTS4 (b): probe port resolved from ``manager.ini``.

        Real apply pipeline orchestrator runs (no monkey-patch on
        ``run_apply_pipeline_if_needed``); the subprocess layer is mocked
        so no real Django process is spawned.
        """
        _write_pending(tmp_path, topology="manager_worker")
        _write_topology(tmp_path, "manager_worker")
        _write_manager_ini(tmp_path, port=8085)

        run_mock = self._stub_subprocess(mocker)

        # Mock the cert helper + caddy start at the caddy_wiring boundary.
        # ``prepare_manager_caddy_and_resolve_port`` is the real seam the
        # launcher hits, so we patch its two collaborators directly to
        # exercise the real bundling logic.
        ensure_cert = mocker.patch(
            "launcher.caddy_wiring.ensure_manager_tls_cert",
            return_value=(
                tmp_path / "manager" / "tls" / "cert.pem",
                tmp_path / "manager" / "tls" / "key.pem",
            ),
        )
        start_caddy = mocker.patch(
            "launcher.caddy_wiring.start_caddy_supervisor",
        )
        wait_mock = mocker.patch(
            "launcher.wizard_runtime.wait_for_runtime_port_bind",
            return_value=True,
        )
        mocker.patch(
            "launcher.wizard_runtime.wizard_dir.cleanup_wizard_dir",
        )

        runtime_proc = FakeProc()
        rc = wizard_runtime.hand_off_to_runtime(
            payload={"topology": "manager_worker"},
            data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=FakeProc(),
            bootstrap_first_run=mocker.MagicMock(
                return_value=tmp_path / "manager",
            ),
            start_component=mocker.MagicMock(return_value=runtime_proc),
        )

        assert rc == 0
        # (a) Caddy is started against <data_dir>/manager/
        ensure_cert.assert_called_once_with(tmp_path / "manager")
        start_caddy.assert_called_once_with(tmp_path / "manager")
        # (b) probe port is the manager.ini-configured value
        wait_mock.assert_called_once()
        assert wait_mock.call_args.args[1] == 8085, (
            "Probe port must come from manager.ini [server] port, "
            "not the hardcoded RUNTIME_MANAGER_PORT constant"
        )
        # (c) the real apply pipeline ran (migrate + apply subprocesses)
        assert run_mock.call_count == 2, (
            f"Real apply pipeline must invoke 2 subprocesses, "
            f"got {run_mock.call_count}"
        )
        # FR-TESTS4 spec ref to #195: --data-dir is verbatim, no /manager append.
        apply_argv = run_mock.call_args_list[1].args[0]
        assert "--data-dir" in apply_argv
        idx = apply_argv.index("--data-dir")
        assert apply_argv[idx + 1] == str(tmp_path), (
            f"--data-dir must equal {tmp_path!r} (regression guard #195); "
            f"got {apply_argv[idx + 1]!r}"
        )

    def test_full_handoff_defaults_to_8080_without_manager_ini(
        self, tmp_path, mocker,
    ):
        """FR-PROBE2: when manager.ini is absent, default is preserved (8080)."""
        _write_pending(tmp_path, topology="manager_worker")
        _write_topology(tmp_path, "manager_worker")
        # Intentionally DO NOT write manager.ini — exercise the fallback.
        (tmp_path / "manager").mkdir()

        self._stub_subprocess(mocker)
        mocker.patch(
            "launcher.caddy_wiring.ensure_manager_tls_cert",
            return_value=(
                tmp_path / "manager" / "tls" / "cert.pem",
                tmp_path / "manager" / "tls" / "key.pem",
            ),
        )
        mocker.patch("launcher.caddy_wiring.start_caddy_supervisor")
        wait_mock = mocker.patch(
            "launcher.wizard_runtime.wait_for_runtime_port_bind",
            return_value=True,
        )
        mocker.patch(
            "launcher.wizard_runtime.wizard_dir.cleanup_wizard_dir",
        )

        wizard_runtime.hand_off_to_runtime(
            payload={"topology": "manager_worker"},
            data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=FakeProc(),
            bootstrap_first_run=mocker.MagicMock(
                return_value=tmp_path / "manager",
            ),
            start_component=mocker.MagicMock(return_value=FakeProc()),
        )

        # Default port is the CADDY_PUBLIC_TLS_PORT constant (8080).
        from launcher.setup_helpers import CADDY_PUBLIC_TLS_PORT
        assert wait_mock.call_args.args[1] == CADDY_PUBLIC_TLS_PORT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
