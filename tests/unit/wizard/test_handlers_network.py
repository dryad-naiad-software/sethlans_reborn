# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``wizard/sethlans_wizard/handlers/network.py`` (FR-M2-3).

Combines the dev agent's smoke pass with coverage expansion: full
session-token gate, request-method gate, oversized body, malformed
JSON, type validation per field, path-traversal hardening
(security-reviewer MED-4) per platform, and bind failure path.
"""

from __future__ import annotations

import configparser
import json
import platform
import socket

import pytest

from wizard.sethlans_wizard import auth_state
from wizard.sethlans_wizard.handlers import network as network_handler

from ._phase1_helpers import VALID_SESSION, auth_env, build_environ, call_handler


@pytest.fixture(autouse=True)
def _reset_auth():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(VALID_SESSION)
    yield
    auth_state.reset_state_for_tests()


@pytest.fixture
def handler(tmp_path):
    return network_handler.make_network_handler(tmp_path)


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class TestHappyPath:

    def test_writes_manager_ini_and_returns_ok(self, handler, tmp_path):
        env = auth_env(
            json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": _free_port(),
                 "data_dir": None},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body
        assert body == {"status": "ok"}
        target = tmp_path / "manager.ini"
        assert target.exists()
        parser = configparser.ConfigParser()
        parser.read(str(target))
        assert parser.get("server", "bind_host") == "127.0.0.1"

    def test_records_network_configured_checkpoint(self, handler, tmp_path):
        # Coverage expansion: the handler MUST drop a checkpoint.
        from wizard.sethlans_wizard import progress
        env = auth_env(
            json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": _free_port()},
            ).encode("utf-8"),
        )
        call_handler(handler, env)
        payload = progress.read_checkpoints(tmp_path)
        assert "network_configured" in payload["checkpoints"]


class TestErrorPaths:

    def test_invalid_port_rejected(self, handler):
        env = auth_env(
            json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": 99999},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert "bind_port" in body["error"].lower()

    def test_zero_port_rejected(self, handler):
        # Coverage expansion: boundary — port 0 must NOT be accepted.
        env = auth_env(
            json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": 0},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body

    def test_bool_port_rejected(self, handler):
        # Coverage expansion: bool is a subtype of int but must NOT pass.
        env = auth_env(
            json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": True},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body

    def test_string_port_rejected(self, handler):
        env = auth_env(
            json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": "8080"},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body

    def test_empty_bind_host_rejected(self, handler):
        env = auth_env(
            json.dumps(
                {"bind_host": "", "bind_port": 8080},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body

    def test_non_string_bind_host_rejected(self, handler):
        env = auth_env(
            json.dumps(
                {"bind_host": 42, "bind_port": 8080},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body

    def test_missing_session_returns_401(self, handler):
        env = build_environ(
            body=json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": 8080},
            ).encode("utf-8"),
        )
        status, _, _ = call_handler(handler, env)
        assert status.startswith("401")

    def test_wrong_session_returns_401(self, handler):
        env = build_environ(
            body=json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": 8080},
            ).encode("utf-8"),
            headers={"X-Wizard-Session": "wrong-session"},
        )
        status, _, _ = call_handler(handler, env)
        assert status.startswith("401")

    def test_get_returns_405_with_allow_header(self, handler):
        env = build_environ(
            method="GET",
            headers={"X-Wizard-Session": VALID_SESSION},
        )
        status, headers, _ = call_handler(handler, env)
        assert status.startswith("405")
        assert headers.get("Allow") == "POST"

    def test_malformed_json_returns_400(self, handler):
        env = auth_env(b"not-json{{")
        status, _, body = call_handler(handler, env)
        assert status.startswith("400")
        assert "json" in body["error"].lower()

    def test_oversized_body_returns_400(self, handler):
        env = auth_env(b"\x00" * 100)
        env["CONTENT_LENGTH"] = "999999"
        status, _, body = call_handler(handler, env)
        assert status.startswith("400")
        assert "large" in body["error"].lower()

    def test_qs_token_rejected(self, handler):
        env = build_environ(
            body=json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": 8080},
            ).encode("utf-8"),
            headers={"X-Wizard-Session": VALID_SESSION},
            query_string="session_token=abc",
        )
        status, _, _ = call_handler(handler, env)
        assert status.startswith("400")

    def test_bind_failure_returns_400(self, handler, mocker):
        # Coverage expansion: bind_failed surfaces a documented category.
        # We mock the socket bind to raise — never block the test.
        mocker.patch.object(
            network_handler, "_try_bind", return_value="bind_failed",
        )
        env = auth_env(
            json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": 8080},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400")
        assert body["error"] == "bind_failed"

    def test_manager_ini_write_failure_returns_500(
        self, handler, mocker, tmp_path,
    ):
        mocker.patch.object(
            network_handler, "_try_bind", return_value=None,
        )
        mocker.patch(
            "wizard.sethlans_wizard.handlers.network.update_manager_ini",
            side_effect=OSError("disk full"),
        )
        env = auth_env(
            json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": 8080},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("500")
        assert "manager.ini" in body["error"].lower()


class TestDataDirValidation:
    """FR-M2-3 / security-reviewer MED-4 — path-traversal hardening."""

    def test_relative_path_rejected(self):
        canonical, code = network_handler.validate_data_dir("./tmp")
        assert canonical is None
        assert code == "relative"

    def test_empty_string_rejected(self):
        # Coverage expansion: empty string also "relative".
        canonical, code = network_handler.validate_data_dir("")
        assert canonical is None
        assert code == "relative"

    def test_bare_filename_rejected(self):
        # Coverage expansion: a bare relative filename.
        canonical, code = network_handler.validate_data_dir("foo")
        assert canonical is None
        assert code == "relative"

    def test_traversal_segment_rejected(self):
        canonical, code = network_handler.validate_data_dir(
            "/usr/local/../etc",
        )
        assert canonical is None
        assert code == "traversal"

    def test_double_dot_in_middle_rejected(self):
        canonical, code = network_handler.validate_data_dir("/foo/../bar")
        assert canonical is None
        assert code == "traversal"

    def test_trailing_double_dot_rejected(self):
        canonical, code = network_handler.validate_data_dir("/foo/..")
        assert canonical is None
        assert code == "traversal"

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="POSIX-only forbidden roots",
    )
    @pytest.mark.parametrize(
        "denied",
        ["/etc", "/proc", "/sys", "/dev", "/root", "/boot"],
    )
    def test_posix_forbidden_roots_rejected(self, denied):
        canonical, code = network_handler.validate_data_dir(denied)
        assert canonical is None
        assert code == "forbidden_root"

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="POSIX-only forbidden roots",
    )
    def test_posix_forbidden_subdirectory_rejected(self):
        canonical, code = network_handler.validate_data_dir(
            "/etc/sethlans",
        )
        assert canonical is None
        assert code == "forbidden_root"

    @pytest.mark.skipif(
        platform.system() != "Windows",
        reason="Windows-only device-namespace prefix",
    )
    def test_windows_device_namespace_rejected(self):
        canonical, code = network_handler.validate_data_dir(
            r"\\?\C:\foo",
        )
        assert canonical is None
        assert code == "device_namespace"

    @pytest.mark.skipif(
        platform.system() != "Windows",
        reason="Windows-only device-namespace prefix",
    )
    def test_windows_dot_device_namespace_rejected(self):
        canonical, code = network_handler.validate_data_dir(
            r"\\.\C:\foo",
        )
        assert canonical is None
        assert code == "device_namespace"

    @pytest.mark.skipif(
        platform.system() != "Windows",
        reason="Windows forbidden roots",
    )
    def test_windows_system_root_rejected(self):
        canonical, code = network_handler.validate_data_dir(
            r"C:\Windows\System32",
        )
        assert canonical is None
        assert code == "forbidden_root"

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="POSIX-only valid path",
    )
    def test_valid_absolute_path_accepted(self, tmp_path):
        canonical, code = network_handler.validate_data_dir(str(tmp_path))
        assert code is None
        assert canonical is not None

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="POSIX-only symlink semantics",
    )
    def test_symlink_resolving_to_denied_root_rejected(
        self, tmp_path,
    ):
        # Coverage expansion / security MED-4: a symlink whose target
        # resolves into a denied root MUST be rejected via realpath.
        link = tmp_path / "link_to_etc"
        try:
            link.symlink_to("/etc")
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not permitted in this environment")
        canonical, code = network_handler.validate_data_dir(str(link))
        # Resolving through the symlink yields /etc which is denied.
        assert canonical is None
        assert code == "forbidden_root"


class TestHandlerOverrideDataDir:
    """FR-M2-3 — submitting a data_dir override routes the manager.ini
    write to the canonicalized override path, not the wizard's current
    data dir."""

    def test_override_writes_to_override_dir(self, tmp_path, mocker):
        # Use a sub-tmpdir as the override and prove manager.ini lands
        # there instead of the handler's bound data_dir.
        override = tmp_path / "override"
        override.mkdir()
        handler = network_handler.make_network_handler(tmp_path)
        mocker.patch.object(
            network_handler, "_try_bind", return_value=None,
        )
        env = auth_env(
            json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": 8080,
                 "data_dir": str(override)},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body
        # manager.ini lands under the override.
        assert (override / "manager.ini").exists()

    def test_override_invalid_returns_400(self, handler):
        # Coverage expansion: a relative override returns the validation
        # error code in the response.
        env = auth_env(
            json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": 8080,
                 "data_dir": "./relative/no"},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "data_dir_invalid"
        assert body["category"] in ("relative", "traversal", "forbidden_root")
