# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/bootstrap.py`` —
serve-with-port-scan and signal-handler facets.

Exercises the FR-W3 port-scan loop and the FR-W17 SIGTERM handler
installation in isolation from the entry-point script.

The secrets / port-file / logging facets live in
``test_bootstrap_secrets.py`` so each file stays under the 300-line
ceiling enforced by CLAUDE.md.
"""

from __future__ import annotations

from pathlib import Path

from wizard.sethlans_wizard import bootstrap, server


# ---------------------------------------------------------------------
# serve_with_port_scan (FR-W3)
# ---------------------------------------------------------------------

class TestServeWithPortScan:

    def test_env_port_uses_single_port(self, tmp_path, monkeypatch):
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        attempts: list[int] = []

        def fake_run(app, host, port, cert_path, key_path):
            attempts.append(port)
            return None  # clean shutdown

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        rc = bootstrap.serve_with_port_scan(
            FakeApp(),
            Path("cert"), Path("key"),
            env_port=8103,
            subdir=subdir,
        )
        assert rc == 0
        assert attempts == [8103]
        assert (subdir / "port").read_text().strip() == "8103"

    def test_env_port_bind_failure_returns_2(
        self, tmp_path, monkeypatch,
    ):
        subdir = tmp_path / "wizard"
        subdir.mkdir()

        def fake_run(app, host, port, cert_path, key_path):
            raise OSError("port in use")

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        rc = bootstrap.serve_with_port_scan(
            FakeApp(),
            Path("cert"), Path("key"),
            env_port=8100,
            subdir=subdir,
        )
        assert rc == 2

    def test_default_scans_until_one_binds(
        self, tmp_path, monkeypatch,
    ):
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        attempts: list[int] = []

        def fake_run(app, host, port, cert_path, key_path):
            attempts.append(port)
            if port < 8102:
                raise OSError("port in use")
            return None  # binds on third candidate

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        rc = bootstrap.serve_with_port_scan(
            FakeApp(),
            Path("cert"), Path("key"),
            env_port=None,
            subdir=subdir,
        )
        assert rc == 0
        assert attempts == [8100, 8101, 8102]
        assert (subdir / "port").read_text().strip() == "8102"

    def test_default_all_ports_busy_returns_2(
        self, tmp_path, monkeypatch,
    ):
        subdir = tmp_path / "wizard"
        subdir.mkdir()

        def fake_run(app, host, port, cert_path, key_path):
            raise OSError("everything is busy")

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        rc = bootstrap.serve_with_port_scan(
            FakeApp(),
            Path("cert"), Path("key"),
            env_port=None,
            subdir=subdir,
        )
        assert rc == 2

    def test_app_wizard_port_is_restamped(
        self, tmp_path, monkeypatch,
    ):
        """The done-marker port must reflect the port we actually bound."""
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        seen_ports: list[int] = []

        def fake_run(app, host, port, cert_path, key_path):
            seen_ports.append(app._wizard_port)
            if port < 8101:
                raise OSError("nope")
            return None

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        app = FakeApp()
        rc = bootstrap.serve_with_port_scan(
            app, Path("cert"), Path("key"),
            env_port=None, subdir=subdir,
        )
        assert rc == 0
        assert seen_ports == [8100, 8101]
        assert app._wizard_port == 8101


# ---------------------------------------------------------------------
# install_signal_handlers (FR-W17)
# ---------------------------------------------------------------------

class TestInstallSignalHandlers:

    def test_does_not_raise_in_main_thread(self):
        """Bare smoke: running it on the test thread doesn't blow up."""
        # signal.signal raises ValueError on non-main threads; the
        # helper swallows that. On the test main thread, it should
        # install successfully.
        bootstrap.install_signal_handlers()
