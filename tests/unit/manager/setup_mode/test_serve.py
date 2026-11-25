# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``setup_mode.serve``: the exit-after-saved wrapper, drain, the wait loop and the Waitress settings."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from waitress.adjustments import Adjustments
from waitress.parser import HTTPRequestParser
from waitress.utilities import RequestEntityTooLarge

from sethlans_manager.setup_mode import serve, state
from sethlans_manager.setup_mode.views import SAVED_ENVIRON_KEY


class _Body(list):
    closed = False

    def close(self):
        self.closed = True


class _Channel:
    def client_disconnected(self):
        return False


def _wrap(saved: bool, body=None):
    body = _Body([b"x"]) if body is None else body
    wrapper = serve.ExitAfterSaved(lambda environ, start_response: body)
    channel = _Channel()
    environ = {"waitress.client_disconnected": channel.client_disconnected}
    if saved:
        environ[SAVED_ENVIRON_KEY] = True
    return wrapper, wrapper(environ, None), body, channel


class TestExitAfterSaved:

    def test_other_responses_pass_through(self):
        wrapper, result, body, _channel = _wrap(saved=False)
        assert result is body
        result.close()
        assert wrapper.saved.is_set() is False

    def test_saving_response_sets_the_event_on_close(self):
        wrapper, result, body, channel = _wrap(saved=True)
        assert list(result) == [b"x"]
        assert wrapper.saved.is_set() is False
        result.close()
        assert body.closed is True
        assert wrapper.saved.is_set() is True
        assert wrapper.channel is channel

    def test_event_is_set_even_when_the_body_close_fails(self):
        class Failing(_Body):
            def close(self):
                raise OSError("gone")

        wrapper, result, _body, _channel = _wrap(saved=True, body=Failing([b"x"]))
        with pytest.raises(OSError):
            result.close()
        assert wrapper.saved.is_set() is True

    def test_body_without_close(self):
        wrapper = serve.ExitAfterSaved(lambda environ, start_response: iter([b"y"]))
        result = wrapper({SAVED_ENVIRON_KEY: True}, None)
        result.close()
        assert wrapper.saved.is_set() and wrapper.channel is None


class TestDrain:

    def test_no_channels(self):
        assert serve.drain(SimpleNamespace(active_channels={}), object(), timeout=0) is True

    def test_saving_channel_gone(self):
        server = SimpleNamespace(active_channels={1: object()})
        assert serve.drain(server, object(), timeout=0) is True

    def test_saving_channel_still_open_times_out(self):
        channel = object()
        assert serve.drain(SimpleNamespace(active_channels={1: channel}), channel, timeout=0) is False

    def test_unknown_channel_waits_for_all(self):
        assert serve.drain(SimpleNamespace(active_channels={1: object()}), None, timeout=0) is False

    def test_waits_until_the_channel_leaves(self, monkeypatch):
        channel = object()
        server = SimpleNamespace(active_channels={1: channel})
        monkeypatch.setattr(serve.time, "sleep", lambda _s: server.active_channels.clear())
        assert serve.drain(server, channel, timeout=60) is True


class TestWait:

    def test_saved(self):
        wrapper = serve.ExitAfterSaved(None)
        wrapper.saved.set()
        assert serve._wait(wrapper, MagicMock()) == serve.EXIT_SAVED

    def test_stopped_by_a_signal(self, monkeypatch):
        monkeypatch.setattr(serve, "POLL_SECONDS", 0.001)
        stop = MagicMock()
        stop.is_set.return_value = True
        assert serve._wait(serve.ExitAfterSaved(None), stop) == serve.EXIT_STOPPED

    def test_exits_saved_when_close_never_comes(self, monkeypatch):
        monkeypatch.setattr(serve, "POLL_SECONDS", 0.001)
        monkeypatch.setattr(state, "STATE", SimpleNamespace(saved_for=lambda: serve.SAVED_FALLBACK_SECONDS + 1))
        stop = MagicMock()
        stop.is_set.return_value = False
        assert serve._wait(serve.ExitAfterSaved(None), stop) == serve.EXIT_SAVED


@pytest.fixture
def created(mocker):
    server = MagicMock(name="server")
    create = mocker.patch.object(serve.waitress, "create_server", return_value=server)
    limit = mocker.patch.object(serve, "limit_request_bodies", side_effect=lambda srv, allow_uploads: srv)
    return create, limit, server


class TestCreateServer:

    def test_waitress_settings(self, created):
        create, limit, server = created
        app = object()
        assert serve.create_server(app, 18090) is server
        args, kwargs = create.call_args
        assert args == (app,)
        assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == 18090
        assert (kwargs["trusted_proxy"], kwargs["trusted_proxy_count"]) == ("127.0.0.1", 1)
        assert kwargs["trusted_proxy_headers"] == {"x-forwarded-for"}
        assert kwargs["max_request_body_size"] == 65537
        limit.assert_called_once_with(server, allow_uploads=False)

    @pytest.mark.parametrize("size, refused", [(65536, False), (65537, True)])
    def test_body_cap_with_those_settings(self, created, size, refused):
        create, _limit, _server = created
        serve.create_server(object(), 18090)
        adj = Adjustments(max_request_body_size=create.call_args.kwargs["max_request_body_size"])
        parser = HTTPRequestParser(adj)
        data = b"POST /api/setup/submit/ HTTP/1.1\r\nHost: a\r\nContent-Length: %d\r\n\r\n" % size + b"a" * size
        while data and not parser.completed:
            data = data[parser.received(data):]
        assert parser.completed is True
        assert isinstance(parser.error, RequestEntityTooLarge) is refused

    def test_forwarded_for_from_caddy_is_the_client(self, created):
        create, _limit, _server = created
        serve.create_server(object(), 18090)
        given = create.call_args.kwargs
        kwargs = {key: given[key] for key in ("trusted_proxy", "trusted_proxy_count", "trusted_proxy_headers")}
        kwargs["clear_untrusted"] = given["clear_untrusted_proxy_headers"]
        from waitress.proxy_headers import proxy_headers_middleware

        seen = {}

        def app(environ, start_response):
            seen["remote"] = environ["REMOTE_ADDR"]
            return []

        wrapped = proxy_headers_middleware(app, log_untrusted=False, **kwargs)
        wrapped({"REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": "192.168.77.10", "wsgi.url_scheme": "http",
                 "SERVER_NAME": "x", "SERVER_PORT": "80"}, lambda *a: None)
        assert seen["remote"] == "192.168.77.10"


class TestRun:

    @pytest.fixture(autouse=True)
    def _no_app_or_signals(self, mocker):
        mocker.patch.object(serve, "build_app", side_effect=lambda: serve.ExitAfterSaved(None))
        mocker.patch.object(serve, "_install_signal_handlers")

    def test_port_that_cannot_be_bound_is_exit_2(self, mocker):
        mocker.patch.object(serve, "create_server", side_effect=OSError(98, "in use"))
        assert serve.run(18090) == serve.EXIT_FAILED

    def test_saved_exits_10_and_closes_the_server(self, mocker):
        server = MagicMock(active_channels={})
        mocker.patch.object(serve, "create_server", return_value=server)
        mocker.patch.object(serve, "_wait", return_value=serve.EXIT_SAVED)
        assert serve.run(18090) == serve.EXIT_SAVED
        server.close.assert_called_once()
        server.task_dispatcher.shutdown.assert_called_once_with(timeout=2)

    def test_stopped_exits_0_without_draining(self, mocker):
        server = MagicMock(active_channels={})
        mocker.patch.object(serve, "create_server", return_value=server)
        mocker.patch.object(serve, "_wait", return_value=serve.EXIT_STOPPED)
        drain = mocker.patch.object(serve, "drain")
        assert serve.run(18090) == serve.EXIT_STOPPED
        drain.assert_not_called()
        server.close.assert_called_once()
