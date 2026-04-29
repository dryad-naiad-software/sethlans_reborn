# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``wizard/sethlans_wizard/handlers/verify.py`` (FR-M2-8 /
FR-M2-8a).

Combines the dev agent's smoke pass with coverage expansion: each
individual check returns the documented shape, topology determines
checklist length (manager: 4 / manager_worker: 5 / worker_only: 4),
the per-handler lock + 60-second cache prevents repeated heavy work,
verified checkpoint is recorded only when ALL checks pass.
"""

from __future__ import annotations

import configparser
import json
import socket

import pytest

from wizard.sethlans_wizard import auth_state, ffmpeg_download, wizard_state
from wizard.sethlans_wizard.handlers import verify as verify_handler
from wizard.sethlans_wizard.handlers.topology import write_topology_atomic

from ._phase1_helpers import VALID_SESSION, auth_env, build_environ, call_handler


@pytest.fixture(autouse=True)
def _reset_state():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(VALID_SESSION)
    wizard_state.reset_state_for_tests()
    verify_handler.reset_cache_for_tests()
    yield
    auth_state.reset_state_for_tests()
    wizard_state.reset_state_for_tests()
    verify_handler.reset_cache_for_tests()


@pytest.fixture
def handler(tmp_path):
    return verify_handler.make_verify_handler(tmp_path)


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _provision_manager_ini(tmp_path, port: int | None = None) -> None:
    """Write a working manager.ini for the network + database checks."""
    parser = configparser.ConfigParser()
    parser["server"] = {
        "bind_host": "127.0.0.1",
        "bind_port": str(port or _free_port()),
    }
    parser["database"] = {
        "engine": "sqlite", "name": "sethlans.db",
    }
    with open(tmp_path / "manager.ini", "w", encoding="utf-8") as fh:
        parser.write(fh)


class TestChecklistShape:

    def test_returns_check_list_shape(self, handler):
        env = auth_env(b"")
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body
        assert "checks" in body
        assert "all_passed" in body
        assert isinstance(body["checks"], list)
        assert isinstance(body["all_passed"], bool)
        names = {c["name"] for c in body["checks"]}
        assert names == {
            "network_bindable",
            "database_reachable",
            "ffmpeg_runs",
            "pending_setup_writable",
        }

    def test_each_check_dict_has_required_fields(self, handler):
        env = auth_env(b"")
        _, _, body = call_handler(handler, env)
        for c in body["checks"]:
            assert set(c.keys()) == {"name", "passed", "error"}
            assert isinstance(c["passed"], bool)


class TestTopologyBranching:
    """FR-M2-8 — manager_worker adds the worker_password_hashed check."""

    def test_manager_topology_4_checks(self, handler, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        env = auth_env(b"")
        _, _, body = call_handler(handler, env)
        names = {c["name"] for c in body["checks"]}
        assert "worker_password_hashed" not in names
        assert len(body["checks"]) == 4

    def test_manager_worker_topology_5_checks(self, handler, tmp_path):
        write_topology_atomic(tmp_path, "manager_worker")
        env = auth_env(b"")
        _, _, body = call_handler(handler, env)
        names = {c["name"] for c in body["checks"]}
        assert "worker_password_hashed" in names
        assert len(body["checks"]) == 5

    def test_manager_worker_worker_password_check_passes_when_set(
        self, handler, tmp_path,
    ):
        write_topology_atomic(tmp_path, "manager_worker")
        wizard_state.set_worker_password_hash("a" * 64, "b" * 32)
        env = auth_env(b"")
        _, _, body = call_handler(handler, env)
        wp = next(
            c for c in body["checks"] if c["name"] == "worker_password_hashed"
        )
        assert wp["passed"] is True

    def test_manager_worker_worker_password_check_fails_when_unset(
        self, handler, tmp_path,
    ):
        write_topology_atomic(tmp_path, "manager_worker")
        env = auth_env(b"")
        _, _, body = call_handler(handler, env)
        wp = next(
            c for c in body["checks"] if c["name"] == "worker_password_hashed"
        )
        assert wp["passed"] is False
        assert wp["error"] is not None

    def test_worker_only_falls_back_to_manager_checklist(
        self, handler, tmp_path,
    ):
        # Coverage expansion: worker_only does NOT add the worker pw
        # check (it's strictly a manager_worker requirement).
        write_topology_atomic(tmp_path, "worker_only")
        env = auth_env(b"")
        _, _, body = call_handler(handler, env)
        names = {c["name"] for c in body["checks"]}
        assert "worker_password_hashed" not in names

    def test_unreadable_topology_defaults_to_manager(
        self, handler, tmp_path,
    ):
        # Coverage expansion: a corrupt topology.json defaults to the
        # manager checklist.
        (tmp_path / "topology.json").write_bytes(b"not json{{")
        env = auth_env(b"")
        _, _, body = call_handler(handler, env)
        names = {c["name"] for c in body["checks"]}
        assert "worker_password_hashed" not in names


class TestNetworkBindableCheck:

    def test_missing_server_section_fails(self, tmp_path):
        # Coverage expansion: empty manager.ini → fail with documented
        # error string.
        result = verify_handler._check_network_bindable(
            configparser.ConfigParser(),
        )
        assert result["passed"] is False
        assert "manager.ini" in result["error"]

    def test_bad_port_string_fails(self, tmp_path):
        parser = configparser.ConfigParser()
        parser["server"] = {"bind_host": "127.0.0.1", "bind_port": "notanint"}
        result = verify_handler._check_network_bindable(parser)
        assert result["passed"] is False
        assert "int" in result["error"].lower()

    def test_bind_failure_classified(self, tmp_path, mocker):
        # Coverage expansion: socket bind raises OSError → check fails.
        mocker.patch.object(
            socket.socket, "bind",
            side_effect=OSError("address already in use"),
        )
        parser = configparser.ConfigParser()
        parser["server"] = {"bind_host": "127.0.0.1", "bind_port": "8080"}
        result = verify_handler._check_network_bindable(parser)
        assert result["passed"] is False

    def test_bind_success(self, tmp_path):
        parser = configparser.ConfigParser()
        parser["server"] = {
            "bind_host": "127.0.0.1", "bind_port": str(_free_port()),
        }
        result = verify_handler._check_network_bindable(parser)
        assert result["passed"] is True


class TestDatabaseReachableCheck:

    def test_missing_database_section_fails(self, tmp_path):
        result = verify_handler._check_database_reachable(
            configparser.ConfigParser(), tmp_path,
        )
        assert result["passed"] is False
        assert "manager.ini" in result["error"]

    def test_sqlite_reachable_passes(self, tmp_path):
        parser = configparser.ConfigParser()
        parser["database"] = {"engine": "sqlite", "name": "x.db"}
        result = verify_handler._check_database_reachable(parser, tmp_path)
        assert result["passed"] is True

    def test_postgresql_unreachable_carries_category(
        self, tmp_path, mocker,
    ):
        mocker.patch(
            "wizard.sethlans_wizard.handlers.database._live_connect",
            return_value=(False, "host_unreachable"),
        )
        parser = configparser.ConfigParser()
        parser["database"] = {
            "engine": "postgresql", "name": "n", "host": "h", "port": "5432",
            "user": "u", "password": "p",
        }
        result = verify_handler._check_database_reachable(parser, tmp_path)
        assert result["passed"] is False
        assert result["error"] == "host_unreachable"


class TestFFmpegRunsCheck:

    def test_no_binary_fails(self, tmp_path, mocker):
        mocker.patch.object(
            ffmpeg_download, "get_ffmpeg_binary", return_value=None,
        )
        result = verify_handler._check_ffmpeg_runs(tmp_path)
        assert result["passed"] is False
        assert "binary" in result["error"]

    def test_version_check_failure(self, tmp_path, mocker):
        mocker.patch.object(
            ffmpeg_download, "get_ffmpeg_binary",
            return_value=tmp_path / "ffmpeg",
        )
        mocker.patch.object(
            ffmpeg_download, "run_version_check",
            return_value=(False, ""),
        )
        result = verify_handler._check_ffmpeg_runs(tmp_path)
        assert result["passed"] is False

    def test_version_string_mismatch(self, tmp_path, mocker):
        mocker.patch.object(
            ffmpeg_download, "get_ffmpeg_binary",
            return_value=tmp_path / "ffmpeg",
        )
        mocker.patch.object(
            ffmpeg_download, "run_version_check",
            return_value=(True, "ffmpeg version 6.0.0"),
        )
        result = verify_handler._check_ffmpeg_runs(tmp_path)
        assert result["passed"] is False
        assert "version" in result["error"]

    def test_happy_path(self, tmp_path, mocker):
        mocker.patch.object(
            ffmpeg_download, "get_ffmpeg_binary",
            return_value=tmp_path / "ffmpeg",
        )
        mocker.patch.object(
            ffmpeg_download, "run_version_check",
            return_value=(True, f"ffmpeg version {ffmpeg_download.FFMPEG_VERSION}"),
        )
        result = verify_handler._check_ffmpeg_runs(tmp_path)
        assert result["passed"] is True

    def test_uses_5s_subprocess_timeout(self, tmp_path, mocker):
        # FR-M2-8 — per-check subprocess timeout is 5s.
        mocker.patch.object(
            ffmpeg_download, "get_ffmpeg_binary",
            return_value=tmp_path / "ffmpeg",
        )
        spy = mocker.patch.object(
            ffmpeg_download, "run_version_check",
            return_value=(True, f"ffmpeg version {ffmpeg_download.FFMPEG_VERSION}"),
        )
        verify_handler._check_ffmpeg_runs(tmp_path)
        assert spy.call_args.kwargs.get("timeout") == 5


class TestPendingSetupWritableCheck:

    def test_writable_passes(self, tmp_path):
        result = verify_handler._check_pending_setup_writable(tmp_path)
        assert result["passed"] is True

    def test_unwritable_fails(self, tmp_path, mocker):
        mocker.patch(
            "pathlib.Path.write_text",
            side_effect=OSError("readonly fs"),
        )
        result = verify_handler._check_pending_setup_writable(tmp_path)
        assert result["passed"] is False
        assert "data_dir" in result["error"]


class TestVerifiedCheckpoint:

    def test_all_passed_records_verified(self, handler, tmp_path, mocker):
        write_topology_atomic(tmp_path, "manager")
        _provision_manager_ini(tmp_path)
        # Stub ffmpeg + writable check so all four pass.
        mocker.patch.object(
            ffmpeg_download, "get_ffmpeg_binary",
            return_value=tmp_path / "ffmpeg",
        )
        mocker.patch.object(
            ffmpeg_download, "run_version_check",
            return_value=(True, f"ffmpeg version {ffmpeg_download.FFMPEG_VERSION}"),
        )
        env = auth_env(b"")
        _, _, body = call_handler(handler, env)
        assert body["all_passed"] is True
        # Verified checkpoint dropped.
        from wizard.sethlans_wizard import progress
        payload = progress.read_checkpoints(tmp_path)
        assert "verified" in payload["checkpoints"]

    def test_any_check_fails_no_verified_checkpoint(
        self, handler, tmp_path,
    ):
        # Empty data dir → checks fail → no verified checkpoint.
        env = auth_env(b"")
        _, _, body = call_handler(handler, env)
        assert body["all_passed"] is False
        from wizard.sethlans_wizard import progress
        payload = progress.read_checkpoints(tmp_path)
        assert "verified" not in (payload.get("checkpoints") or [])


class TestCachingAndConcurrency:
    """FR-M2-8a — verify-in-progress lock + 60-second result cache."""

    def test_second_call_returns_cached_result(
        self, handler, tmp_path, mocker,
    ):
        # Coverage expansion: the SECOND call within 60s must return
        # the same result without re-running the heavy checks.
        spy = mocker.spy(verify_handler, "_run_checks")
        env = auth_env(b"")
        call_handler(handler, env)
        call_handler(handler, env)
        # _run_checks called only once thanks to the cache.
        assert spy.call_count == 1

    def test_cache_invalidated_via_test_helper(
        self, handler, mocker,
    ):
        spy = mocker.spy(verify_handler, "_run_checks")
        env = auth_env(b"")
        call_handler(handler, env)
        verify_handler.reset_cache_for_tests()
        call_handler(handler, env)
        assert spy.call_count == 2


class TestRequestGates:

    def test_get_returns_405(self, handler):
        env = build_environ(
            method="GET",
            headers={"X-Wizard-Session": VALID_SESSION},
        )
        status, headers, _ = call_handler(handler, env)
        assert status.startswith("405")
        assert headers.get("Allow") == "POST"

    def test_missing_session_returns_401(self, handler):
        env = build_environ(method="POST", body=b"")
        status, _, _ = call_handler(handler, env)
        assert status.startswith("401")


class TestTopologyReader:

    def test_missing_topology_file_returns_none(self, tmp_path):
        assert verify_handler._read_topology(tmp_path) is None

    def test_corrupt_topology_returns_none(self, tmp_path):
        (tmp_path / "topology.json").write_bytes(b"{{ not json")
        assert verify_handler._read_topology(tmp_path) is None

    def test_non_dict_topology_returns_none(self, tmp_path):
        (tmp_path / "topology.json").write_text(
            json.dumps(["array"]), encoding="utf-8",
        )
        assert verify_handler._read_topology(tmp_path) is None

    def test_non_string_topology_field_returns_none(self, tmp_path):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": 42}), encoding="utf-8",
        )
        assert verify_handler._read_topology(tmp_path) is None

    def test_valid_topology_returns_string(self, tmp_path):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": "manager"}), encoding="utf-8",
        )
        assert verify_handler._read_topology(tmp_path) == "manager"


class TestExports:

    def test_dunder_all(self):
        for name in (
            "make_verify_handler",
            "reset_cache_for_tests",
        ):
            assert name in verify_handler.__all__

    def test_constants(self):
        # FR-M2-8 — per-check timeouts MUST be the documented values.
        assert verify_handler.VERIFY_SOCKET_TIMEOUT == 10
        assert verify_handler.VERIFY_SUBPROCESS_TIMEOUT == 5
        assert verify_handler.VERIFY_RESULT_CACHE_SECONDS == 60
