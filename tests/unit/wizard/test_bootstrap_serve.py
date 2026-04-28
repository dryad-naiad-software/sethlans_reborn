# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/bootstrap.py`` —
serve-with-port-scan and signal-handler facets.

Exercises the FR-W3 port-scan loop and the FR-W17 SIGTERM handler
installation in isolation from the entry-point script.

Issue #170: TLS plumbing moved to the launcher's Caddy supervisor;
``serve_with_port_scan`` no longer takes cert/key paths and ``server.run``
takes only ``(app, host, port)``.

The secrets / port-file / logging facets live in
``test_bootstrap_secrets.py`` so each file stays under the 300-line
ceiling enforced by CLAUDE.md.
"""

from __future__ import annotations

from wizard.sethlans_wizard import bootstrap, server


# ---------------------------------------------------------------------
# serve_with_port_scan (FR-W3)
# ---------------------------------------------------------------------

class TestServeWithPortScan:

    def test_env_port_uses_single_port(self, tmp_path, monkeypatch):
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        attempts: list[int] = []

        def fake_run(app, host, port):
            attempts.append(port)
            return None  # clean shutdown

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        rc = bootstrap.serve_with_port_scan(
            FakeApp(),
            env_port=8103,
            subdir=subdir,
        )
        assert rc == 0
        assert attempts == [8103]
        # Issue #170: the wizard now writes its loopback port to
        # ``loopback_port``; the public-facing ``port`` file is owned
        # by the launcher post-Caddy-up.
        assert (subdir / "loopback_port").read_text().strip() == "8103"
        assert not (subdir / "port").exists()

    def test_env_port_bind_failure_returns_2(
        self, tmp_path, monkeypatch,
    ):
        subdir = tmp_path / "wizard"
        subdir.mkdir()

        def fake_run(app, host, port):
            raise OSError("port in use")

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        rc = bootstrap.serve_with_port_scan(
            FakeApp(),
            env_port=8099,
            subdir=subdir,
        )
        assert rc == 2

    def test_default_scans_until_one_binds(
        self, tmp_path, monkeypatch,
    ):
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        attempts: list[int] = []

        def fake_run(app, host, port):
            attempts.append(port)
            # The scan range is (8099, 8101, 8102, 8103, 8104) — the
            # third candidate is 8102.
            if port < 8102:
                raise OSError("port in use")
            return None  # binds on third candidate

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        rc = bootstrap.serve_with_port_scan(
            FakeApp(),
            env_port=None,
            subdir=subdir,
        )
        assert rc == 0
        assert attempts == [8099, 8101, 8102]
        assert (subdir / "loopback_port").read_text().strip() == "8102"

    def test_default_all_ports_busy_returns_2(
        self, tmp_path, monkeypatch,
    ):
        subdir = tmp_path / "wizard"
        subdir.mkdir()

        def fake_run(app, host, port):
            raise OSError("everything is busy")

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        rc = bootstrap.serve_with_port_scan(
            FakeApp(),
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

        def fake_run(app, host, port):
            seen_ports.append(app._wizard_port)
            # First scan candidate (8099) raises; second (8101) binds.
            if port < 8101:
                raise OSError("nope")
            return None

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        app = FakeApp()
        rc = bootstrap.serve_with_port_scan(
            app, env_port=None, subdir=subdir,
        )
        assert rc == 0
        assert seen_ports == [8099, 8101]
        assert app._wizard_port == 8101

    def test_run_called_with_loopback_host(
        self, tmp_path, monkeypatch,
    ):
        """Issue #170 / NFR-6: wizard binds 127.0.0.1 only — never 0.0.0.0."""
        subdir = tmp_path / "wizard"
        subdir.mkdir()
        seen_hosts: list[str] = []

        def fake_run(app, host, port):
            seen_hosts.append(host)
            return None

        monkeypatch.setattr(server, "run", fake_run)

        class FakeApp:
            _wizard_port = 0

        bootstrap.serve_with_port_scan(
            FakeApp(), env_port=8099, subdir=subdir,
        )
        assert seen_hosts == ["127.0.0.1"]


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
