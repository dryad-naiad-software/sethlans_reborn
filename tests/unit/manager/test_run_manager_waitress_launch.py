# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``sethlans_manager.waitress_launcher.launch`` — the
dual-listener happy path.

Split from ``test_run_manager_waitress.py`` to respect the 300-line
Python file ceiling. Exercises:

* Two ``waitress.create_server(...)`` calls, one per loopback port,
  both bound to ``127.0.0.1``, both tagged with the correct ``ident``
  and the spec-fixed tuning forwarded verbatim.
* Each listener thread is a daemon with a readable ``name``.
* Shutdown drains both listeners via ``server.close()`` and joins the
  threads.
"""

from __future__ import annotations

from sethlans_manager import waitress_launcher


class TestLaunchDualListeners:

    def setup_method(self):
        waitress_launcher._servers.clear()
        waitress_launcher._threads.clear()
        waitress_launcher._shutdown_event.clear()

    def test_two_waitress_servers_with_correct_binds(
        self, mocker, tmp_path,
    ):
        """``launch`` must create exactly two Waitress servers, both
        bound to loopback, each tagged with the correct ``ident``, and
        the tuning kwargs from ``get_waitress_tuning`` forwarded
        verbatim."""
        fake_server_a = mocker.MagicMock(name="public_server")
        fake_server_b = mocker.MagicMock(name="internal_server")
        create_server = mocker.patch.object(
            waitress_launcher.waitress, "create_server",
            side_effect=[fake_server_a, fake_server_b],
        )
        tuning = {
            "threads": 16,
            "channel_timeout": 300,
            "connection_limit": 1000,
            "max_request_body_size": 104857600,
        }
        mocker.patch.object(
            waitress_launcher, "get_waitress_tuning",
            return_value=tuning,
        )
        mocker.patch(
            "sethlans_manager.wsgi.application", new=object(),
        )
        thread_cls = mocker.patch.object(
            waitress_launcher.threading, "Thread",
        )
        thread_cls.side_effect = lambda **kwargs: mocker.MagicMock(
            name=kwargs.get("name"),
        )
        mocker.patch.object(
            waitress_launcher, "_install_signal_handlers",
        )
        waitress_launcher._shutdown_event.set()

        waitress_launcher.launch(
            public_port=8090,
            internal_port=8088,
            ini_path=tmp_path / "manager.ini",
        )

        assert create_server.call_count == 2
        first = create_server.call_args_list[0].kwargs
        assert first["host"] == "127.0.0.1"
        assert first["port"] == 8090
        assert first["ident"] == "sethlans-manager-public"
        assert first["threads"] == 16
        assert first["channel_timeout"] == 300
        assert first["connection_limit"] == 1000
        assert first["max_request_body_size"] == 104857600
        assert "trusted_proxy" not in first
        second = create_server.call_args_list[1].kwargs
        assert second["host"] == "127.0.0.1"
        assert second["port"] == 8088
        assert second["ident"] == "sethlans-manager-loopback"

    def test_listener_threads_are_daemons_with_readable_names(
        self, mocker, tmp_path,
    ):
        mocker.patch.object(
            waitress_launcher.waitress, "create_server",
            side_effect=[mocker.MagicMock(), mocker.MagicMock()],
        )
        mocker.patch.object(
            waitress_launcher, "get_waitress_tuning",
            return_value={
                "threads": 16,
                "channel_timeout": 300,
                "connection_limit": 1000,
                "max_request_body_size": 104857600,
            },
        )
        mocker.patch(
            "sethlans_manager.wsgi.application", new=object(),
        )
        thread_cls = mocker.patch.object(
            waitress_launcher.threading, "Thread",
        )
        thread_cls.side_effect = lambda **kwargs: mocker.MagicMock(
            name=kwargs.get("name"),
        )
        mocker.patch.object(
            waitress_launcher, "_install_signal_handlers",
        )
        waitress_launcher._shutdown_event.set()

        waitress_launcher.launch(
            public_port=8090,
            internal_port=8088,
            ini_path=tmp_path / "manager.ini",
        )

        assert thread_cls.call_count == 2
        public_kwargs = thread_cls.call_args_list[0].kwargs
        internal_kwargs = thread_cls.call_args_list[1].kwargs
        assert public_kwargs["daemon"] is True
        assert internal_kwargs["daemon"] is True
        assert public_kwargs["name"] == "manager-waitress-public"
        assert internal_kwargs["name"] == "manager-waitress-loopback"

    def test_shutdown_drains_both_listeners(
        self, mocker, tmp_path,
    ):
        """After ``launch`` returns, both ``server.close()`` must have
        been invoked (the shared drain helper handles join)."""
        srv_a = mocker.MagicMock(name="public")
        srv_b = mocker.MagicMock(name="internal")
        mocker.patch.object(
            waitress_launcher.waitress, "create_server",
            side_effect=[srv_a, srv_b],
        )
        mocker.patch.object(
            waitress_launcher, "get_waitress_tuning",
            return_value={
                "threads": 16,
                "channel_timeout": 300,
                "connection_limit": 1000,
                "max_request_body_size": 104857600,
            },
        )
        mocker.patch(
            "sethlans_manager.wsgi.application", new=object(),
        )
        fake_threads = [mocker.MagicMock(), mocker.MagicMock()]
        for t in fake_threads:
            t.is_alive.return_value = False
        thread_cls = mocker.patch.object(
            waitress_launcher.threading, "Thread",
        )
        thread_cls.side_effect = fake_threads
        mocker.patch.object(
            waitress_launcher, "_install_signal_handlers",
        )
        waitress_launcher._shutdown_event.set()

        waitress_launcher.launch(
            public_port=8090,
            internal_port=8088,
            ini_path=tmp_path / "manager.ini",
        )
        srv_a.close.assert_called_once()
        srv_b.close.assert_called_once()
        for t in fake_threads:
            t.join.assert_called_once()
