# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Smoke tests for ``wizard/sethlans_wizard/handlers/network.py``
(FR-M2-3)."""

from __future__ import annotations

import configparser
import json

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


class TestHappyPath:

    def test_writes_manager_ini_and_returns_ok(self, handler, tmp_path):
        # Pick port 0 so the OS picks a free ephemeral port — but the
        # handler validates the supplied port via socket.bind(); use a
        # high-unprivileged port and accept that an EADDRINUSE collision
        # is unlikely on test runners.
        env = auth_env(
            json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": 0, "data_dir": None},
            ).encode("utf-8"),
        )
        # bind_port=0 fails the validator (must be 1..65535). Use a real
        # ephemeral port instead — bind to (127.0.0.1, 0) here just to
        # discover one, then close.
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
        sock.close()
        env = auth_env(
            json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": free_port,
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
        assert parser.getint("server", "bind_port") == free_port


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

    def test_missing_session_returns_401(self, handler):
        env = build_environ(
            body=json.dumps(
                {"bind_host": "127.0.0.1", "bind_port": 8080},
            ).encode("utf-8"),
        )
        status, _, _ = call_handler(handler, env)
        assert status.startswith("401")


class TestDataDirValidation:

    def test_relative_path_rejected(self):
        canonical, code = network_handler.validate_data_dir("./tmp")
        assert canonical is None
        assert code == "relative"

    def test_traversal_path_rejected(self):
        canonical, code = network_handler.validate_data_dir(
            "/usr/local/../etc",
        )
        assert canonical is None
        assert code == "traversal"
