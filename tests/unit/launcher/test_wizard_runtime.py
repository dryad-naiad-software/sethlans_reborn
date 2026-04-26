# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for ``launcher.wizard_runtime`` (FR-L7 / FR-L7b / FR-L10)."""

import json
import subprocess
from unittest.mock import MagicMock

import pytest

from launcher import wizard_ipc, wizard_runtime


SECRET = b"a" * 32


class _FakeProc:
    """Minimal Popen stand-in for the runtime/wizard process."""

    def __init__(self, returncode=None, pid=12345):
        self.returncode = returncode
        self.pid = pid
        self._poll_results = [returncode]
        self.terminate_called = False
        self.kill_called = False
        self.wait_raises = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_called = True

    def kill(self):
        self.kill_called = True

    def wait(self, timeout=None):
        del timeout
        if self.wait_raises:
            raise subprocess.TimeoutExpired(cmd="x", timeout=1)
        return self.returncode or 0


# ---- read_topology_file ---------------------------------------------------

class TestReadTopologyFile:

    def test_returns_topology_string(self, tmp_path):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": "manager_worker"}), encoding="utf-8",
        )
        assert wizard_runtime.read_topology_file(tmp_path) == "manager_worker"

    def test_returns_empty_for_missing(self, tmp_path):
        assert wizard_runtime.read_topology_file(tmp_path) == ""

    def test_returns_empty_for_malformed(self, tmp_path):
        (tmp_path / "topology.json").write_text("not json")
        assert wizard_runtime.read_topology_file(tmp_path) == ""


# ---- spawn_runtime_for_topology -------------------------------------------

class TestSpawnRuntimeForTopology:

    def test_manager_topology_calls_bootstrap_and_spawns_manager(
        self, tmp_path,
    ):
        bootstrap = MagicMock(return_value=tmp_path / "manager")
        proc_obj = _FakeProc()
        start = MagicMock(return_value=proc_obj)
        proc, port = wizard_runtime.spawn_runtime_for_topology(
            "manager", bootstrap, start, tmp_path,
        )
        bootstrap.assert_called_once_with(tmp_path)
        start.assert_called_once_with("manager")
        assert proc is proc_obj
        assert port == wizard_runtime.RUNTIME_MANAGER_PORT

    def test_manager_worker_topology_spawns_manager(self, tmp_path):
        bootstrap = MagicMock()
        proc_obj = _FakeProc()
        start = MagicMock(return_value=proc_obj)
        proc, port = wizard_runtime.spawn_runtime_for_topology(
            "manager_worker", bootstrap, start, tmp_path,
        )
        start.assert_called_once_with("manager")
        assert port == wizard_runtime.RUNTIME_MANAGER_PORT

    def test_worker_only_topology_spawns_worker_no_port(self, tmp_path):
        bootstrap = MagicMock()
        proc_obj = _FakeProc()
        start = MagicMock(return_value=proc_obj)
        proc, port = wizard_runtime.spawn_runtime_for_topology(
            "worker_only", bootstrap, start, tmp_path,
        )
        bootstrap.assert_not_called()
        start.assert_called_once_with("worker")
        assert port is None

    def test_worker_alias_spawns_worker(self, tmp_path):
        start = MagicMock(return_value=_FakeProc())
        _, port = wizard_runtime.spawn_runtime_for_topology(
            "worker", MagicMock(), start, tmp_path,
        )
        start.assert_called_once_with("worker")
        assert port is None

    def test_unknown_topology_returns_nones(self, tmp_path):
        proc, port = wizard_runtime.spawn_runtime_for_topology(
            "bogus", MagicMock(), MagicMock(), tmp_path,
        )
        assert proc is None
        assert port is None


# ---- wait_for_runtime_port_bind -------------------------------------------

class TestWaitForRuntimePortBind:

    def test_returns_true_when_port_binds_and_health_ok(self, mocker):
        proc = _FakeProc()
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
        proc = _FakeProc(returncode=1)
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
        proc = _FakeProc()
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
        proc = _FakeProc()
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


# ---- write_runtime_failed_marker ------------------------------------------

class TestWriteRuntimeFailedMarker:

    def test_writes_valid_marker(self, tmp_path):
        wizard_runtime.write_runtime_failed_marker(
            tmp_path, SECRET, reason="port_bind_timeout",
        )
        marker = tmp_path / "wizard" / wizard_ipc.MARKER_RUNTIME_FAILED
        assert marker.exists()
        result = wizard_ipc.read_marker(
            marker, SECRET, "runtime_failed", tmp_path,
        )
        assert result is not None
        assert result["reason"] == "port_bind_timeout"


# ---- terminate_wizard -----------------------------------------------------

class TestTerminateWizard:

    def test_no_op_when_proc_is_none(self):
        wizard_runtime.terminate_wizard(None)

    def test_no_op_when_already_exited(self):
        proc = _FakeProc(returncode=0)
        wizard_runtime.terminate_wizard(proc)
        assert proc.terminate_called is False

    def test_calls_terminate_then_wait(self):
        proc = _FakeProc()
        wizard_runtime.terminate_wizard(proc)
        assert proc.terminate_called is True

    def test_kills_after_grace_timeout(self):
        proc = _FakeProc()
        proc.wait_raises = True
        wizard_runtime.terminate_wizard(proc)
        assert proc.kill_called is True


# ---- hand_off_to_runtime --------------------------------------------------

class TestHandOffToRuntime:

    def test_success_path_returns_zero_and_cleans_up(
        self, tmp_path, mocker,
    ):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": "worker_only"}), encoding="utf-8",
        )
        wizard_proc = _FakeProc()
        runtime_proc = _FakeProc()
        bootstrap = MagicMock()
        start = MagicMock(return_value=runtime_proc)
        cleanup = mocker.patch(
            "launcher.wizard_runtime.wizard_dir.cleanup_wizard_dir",
        )

        rc = wizard_runtime.hand_off_to_runtime(
            payload={"topology": "worker_only"},
            data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=wizard_proc,
            bootstrap_first_run=bootstrap,
            start_component=start,
            on_manager_ready=None,
        )
        assert rc == 0
        cleanup.assert_called_once_with(tmp_path)

    def test_returns_failure_when_topology_missing(self, tmp_path):
        rc = wizard_runtime.hand_off_to_runtime(
            payload={}, data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=_FakeProc(),
            bootstrap_first_run=MagicMock(),
            start_component=MagicMock(),
            on_manager_ready=None,
        )
        assert rc == 1

    def test_writes_runtime_failed_when_port_bind_fails(
        self, tmp_path, mocker,
    ):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": "manager"}), encoding="utf-8",
        )
        runtime_proc = _FakeProc()
        bootstrap = MagicMock()
        start = MagicMock(return_value=runtime_proc)
        mocker.patch(
            "launcher.wizard_runtime.wait_for_runtime_port_bind",
            return_value=False,
        )
        write_marker = mocker.patch(
            "launcher.wizard_runtime.write_runtime_failed_marker",
        )

        rc = wizard_runtime.hand_off_to_runtime(
            payload={"topology": "manager"},
            data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=_FakeProc(),
            bootstrap_first_run=bootstrap,
            start_component=start,
            on_manager_ready=None,
        )
        assert rc == 1
        write_marker.assert_called_once()

    def test_terminate_wizard_called_before_cleanup_dir(
        self, tmp_path, mocker,
    ):
        """CRITICAL-2 (Phase F1): wizard MUST be terminated BEFORE
        cleanup_wizard_dir rmtrees its working directory."""
        (tmp_path / "topology.json").write_text(
            '{"topology": "worker_only"}', encoding="utf-8",
        )
        order: list[str] = []
        terminate = mocker.patch(
            "launcher.wizard_runtime.terminate_wizard",
            side_effect=lambda *a, **k: order.append("terminate"),
        )
        cleanup = mocker.patch(
            "launcher.wizard_runtime.wizard_dir.cleanup_wizard_dir",
            side_effect=lambda *a, **k: order.append("cleanup"),
        )
        wizard_runtime.hand_off_to_runtime(
            payload={"topology": "worker_only"},
            data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=_FakeProc(),
            bootstrap_first_run=MagicMock(),
            start_component=MagicMock(return_value=_FakeProc()),
            on_manager_ready=None,
        )
        assert order == ["terminate", "cleanup"], (
            "terminate_wizard MUST run before cleanup_wizard_dir "
            "(CRITICAL-2 Phase F1)"
        )
        terminate.assert_called_once()
        cleanup.assert_called_once_with(tmp_path)

    def test_invokes_on_manager_ready_callback(self, tmp_path, mocker):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": "worker_only"}), encoding="utf-8",
        )
        callback = MagicMock()
        wizard_runtime.hand_off_to_runtime(
            payload={"topology": "worker_only"},
            data_dir=tmp_path, ipc_secret=SECRET,
            wizard_proc=_FakeProc(),
            bootstrap_first_run=MagicMock(),
            start_component=MagicMock(return_value=_FakeProc()),
            on_manager_ready=callback,
        )
        callback.assert_called_once()


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
