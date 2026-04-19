# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``sethlans_manager.uvicorn_launcher`` (FR-22)."""

from __future__ import annotations

from sethlans_manager import uvicorn_launcher


class TestLaunchProdMode:

    def test_runs_two_servers_via_asyncio_gather(self, mocker, tmp_path):
        # Capture uvicorn.Config + uvicorn.Server calls.
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

        uvicorn_launcher.launch(
            host="0.0.0.0",
            port=8080,
            cert_path=tmp_path / "cert.pem",
            key_path=tmp_path / "key.pem",
            dev_mode=False,
            manager_dir=tmp_path,
            get_loopback_port=lambda: "8088",
        )

        # Two Config instances created (main HTTPS + loopback plaintext).
        assert fake_config.call_count == 2
        calls = fake_config.call_args_list
        main_kwargs = calls[0].kwargs
        loopback_kwargs = calls[1].kwargs
        assert main_kwargs["host"] == "0.0.0.0"
        assert main_kwargs["port"] == 8080
        assert "ssl_certfile" in main_kwargs
        assert loopback_kwargs["host"] == "127.0.0.1"
        assert loopback_kwargs["port"] == 8088
        # Loopback Config must not have TLS.
        assert "ssl_certfile" not in loopback_kwargs
        assert "ssl_keyfile" not in loopback_kwargs

        # Two Server objects constructed.
        assert fake_server_cls.call_count == 2


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
        # Dev mode: single uvicorn.run call; no Config/Server pairs.
        uv_run.assert_called_once()
        args, kwargs = uv_run.call_args
        assert kwargs.get("reload") is True
        fake_config.assert_not_called()
        fake_server.assert_not_called()
        # Prints a notice about skipped loopback.
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
