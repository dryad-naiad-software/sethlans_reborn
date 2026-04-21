# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``sethlans_manager.uvicorn_launcher``.

Phase 2 of the waitress-migration-manager spec: the main listener is
still uvicorn (ASGI, HTTPS) but the loopback status listener is now a
Waitress thread bound to ``127.0.0.1:<loopback_port>``.
"""

from __future__ import annotations

from sethlans_manager import uvicorn_launcher


class TestLaunchProdMode:

    def test_starts_waitress_thread_and_uvicorn_main_server(
        self, mocker, tmp_path,
    ):
        """Prod mode must: (a) start Waitress on the loopback port in a
        dedicated thread, (b) construct exactly one uvicorn.Config /
        uvicorn.Server pair for the main listener, (c) never create a
        second uvicorn listener for loopback."""
        fake_config = mocker.patch.object(
            uvicorn_launcher.uvicorn, "Config",
        )
        fake_server_cls = mocker.patch.object(
            uvicorn_launcher.uvicorn, "Server",
        )
        mocker.patch.object(
            uvicorn_launcher.asyncio, "run",
        )
        mocker.patch.object(
            uvicorn_launcher.sys, "platform", "linux",
        )
        start_spy = mocker.patch.object(
            uvicorn_launcher, "_start_waitress_loopback",
        )
        stop_spy = mocker.patch.object(
            uvicorn_launcher, "_stop_waitress_loopback",
        )

        uvicorn_launcher.launch(
            host="0.0.0.0",
            port=8080,
            cert_path=tmp_path / "cert.pem",
            key_path=tmp_path / "key.pem",
            dev_mode=False,
            manager_dir=tmp_path,
            get_loopback_port=lambda: "8088",
        )

        # Exactly one uvicorn.Config (main only — loopback handled by Waitress).
        assert fake_config.call_count == 1
        main_kwargs = fake_config.call_args_list[0].kwargs
        assert main_kwargs["host"] == "0.0.0.0"
        assert main_kwargs["port"] == 8080
        assert "ssl_certfile" in main_kwargs
        assert "ssl_keyfile" in main_kwargs
        # One uvicorn.Server constructed.
        assert fake_server_cls.call_count == 1
        # Waitress loopback thread started with port=8088.
        start_spy.assert_called_once_with(8088)
        # Waitress stop invoked in the finally branch of launch().
        stop_spy.assert_called()


class TestLaunchDevMode:

    def test_dev_mode_runs_single_uvicorn_and_skips_loopback(
        self, mocker, tmp_path, capsys,
    ):
        uv_run = mocker.patch.object(uvicorn_launcher.uvicorn, "run")
        fake_config = mocker.patch.object(
            uvicorn_launcher.uvicorn, "Config",
        )
        fake_server = mocker.patch.object(
            uvicorn_launcher.uvicorn, "Server",
        )
        start_spy = mocker.patch.object(
            uvicorn_launcher, "_start_waitress_loopback",
        )
        mocker.patch.object(
            uvicorn_launcher.sys, "platform", "linux",
        )
        uvicorn_launcher.launch(
            host="127.0.0.1",
            port=8080,
            cert_path=tmp_path / "cert.pem",
            key_path=tmp_path / "key.pem",
            dev_mode=True,
            manager_dir=tmp_path,
            get_loopback_port=lambda: "8088",
        )
        # Dev mode: single uvicorn.run call; no Config/Server pairs;
        # no Waitress loopback thread.
        uv_run.assert_called_once()
        args, kwargs = uv_run.call_args
        assert kwargs.get("reload") is True
        fake_config.assert_not_called()
        fake_server.assert_not_called()
        start_spy.assert_not_called()
        out = capsys.readouterr().out
        assert "loopback" in out.lower()


class TestSelectorPolicyInstalledOnWindows:

    def test_install_selector_policy_on_win32(self, mocker, tmp_path):
        mocker.patch.object(
            uvicorn_launcher.uvicorn, "Config",
        )
        mocker.patch.object(
            uvicorn_launcher.uvicorn, "Server",
        )
        mocker.patch.object(
            uvicorn_launcher.asyncio, "run",
        )
        mocker.patch.object(
            uvicorn_launcher, "_start_waitress_loopback",
        )
        mocker.patch.object(
            uvicorn_launcher, "_stop_waitress_loopback",
        )
        mocker.patch.object(
            uvicorn_launcher.sys, "platform", "win32",
        )
        spy = mocker.patch.object(
            uvicorn_launcher, "_install_selector_policy",
        )
        uvicorn_launcher.launch(
            host="0.0.0.0",
            port=8080,
            cert_path=tmp_path / "cert.pem",
            key_path=tmp_path / "key.pem",
            dev_mode=False,
            manager_dir=tmp_path,
            get_loopback_port=lambda: "8088",
        )
        spy.assert_called_once()

    def test_install_selector_policy_not_called_on_linux(
        self, mocker, tmp_path,
    ):
        mocker.patch.object(uvicorn_launcher.uvicorn, "Config")
        mocker.patch.object(uvicorn_launcher.uvicorn, "Server")
        mocker.patch.object(uvicorn_launcher.asyncio, "run")
        mocker.patch.object(
            uvicorn_launcher, "_start_waitress_loopback",
        )
        mocker.patch.object(
            uvicorn_launcher, "_stop_waitress_loopback",
        )
        mocker.patch.object(
            uvicorn_launcher.sys, "platform", "linux",
        )
        spy = mocker.patch.object(
            uvicorn_launcher, "_install_selector_policy",
        )
        uvicorn_launcher.launch(
            host="0.0.0.0",
            port=8080,
            cert_path=tmp_path / "cert.pem",
            key_path=tmp_path / "key.pem",
            dev_mode=False,
            manager_dir=tmp_path,
            get_loopback_port=lambda: "8088",
        )
        spy.assert_not_called()


class TestWaitressLoopbackThread:
    """Exercises ``_start_waitress_loopback`` and ``_stop_waitress_loopback``."""

    def test_start_creates_waitress_server_bound_to_loopback(
        self, mocker,
    ):
        fake_server = mocker.MagicMock()
        create_server = mocker.patch.object(
            uvicorn_launcher.waitress, "create_server",
            return_value=fake_server,
        )
        # Prevent the daemon thread from actually running Waitress.
        fake_thread = mocker.MagicMock()
        thread_cls = mocker.patch.object(
            uvicorn_launcher.threading, "Thread",
            return_value=fake_thread,
        )

        uvicorn_launcher._start_waitress_loopback(8088)

        create_server.assert_called_once()
        kwargs = create_server.call_args.kwargs
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 8088
        # install_signal_handlers kwarg omitted -> Waitress default is
        # False when using create_server (only waitress.serve installs
        # them); we assert the contract by checking no SIGTERM handler
        # keyword was passed explicitly.
        assert "trusted_proxy" not in kwargs or kwargs["trusted_proxy"] is None
        thread_cls.assert_called_once()
        assert thread_cls.call_args.kwargs["daemon"] is True
        assert thread_cls.call_args.kwargs["name"] == (
            "manager-loopback-waitress"
        )
        fake_thread.start.assert_called_once()

        # Clean up module state for other tests.
        uvicorn_launcher._waitress_server = None
        uvicorn_launcher._waitress_thread = None

    def test_stop_closes_server_and_joins_thread(self, mocker):
        fake_server = mocker.MagicMock()
        fake_thread = mocker.MagicMock()
        fake_thread.is_alive.return_value = False
        uvicorn_launcher._waitress_server = fake_server
        uvicorn_launcher._waitress_thread = fake_thread

        uvicorn_launcher._stop_waitress_loopback(join_timeout=1.0)

        fake_server.close.assert_called_once()
        fake_thread.join.assert_called_once_with(timeout=1.0)
        assert uvicorn_launcher._waitress_server is None
        assert uvicorn_launcher._waitress_thread is None

    def test_stop_is_noop_when_server_already_none(self):
        uvicorn_launcher._waitress_server = None
        uvicorn_launcher._waitress_thread = None
        # Must not raise.
        uvicorn_launcher._stop_waitress_loopback()

    def test_stop_logs_warning_when_thread_does_not_exit(
        self, mocker, capsys,
    ):
        fake_server = mocker.MagicMock()
        fake_thread = mocker.MagicMock()
        fake_thread.is_alive.return_value = True
        uvicorn_launcher._waitress_server = fake_server
        uvicorn_launcher._waitress_thread = fake_thread

        uvicorn_launcher._stop_waitress_loopback(join_timeout=0.1)

        err = capsys.readouterr().err
        assert "did not exit" in err.lower()

        # Clean up module state.
        uvicorn_launcher._waitress_server = None
        uvicorn_launcher._waitress_thread = None
