# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The setup-mode server and the exit after the saving response (FR-SM-4).

One Waitress server (``waitress.create_server``) on the manager's public
loopback port, behind the launcher's Caddy, with:

* ``trusted_proxy='127.0.0.1'``, ``trusted_proxy_count=1`` and
  ``trusted_proxy_headers={'x-forwarded-for'}``, so ``REMOTE_ADDR`` is the
  real client Caddy saw, not 127.0.0.1 for everyone (FR-SEC-3);
* a ``max_request_body_size`` of the setup limit plus one (Waitress
  refuses a body of that size or more) and ``limit_request_bodies``;
* none of the running manager's start hooks.

The success response of a submit is streamed, so Waitress closes the
connection after it (``Connection: close``). :class:`ExitAfterSaved`
wraps the app: when the server closes that response's body, an event is
set. The main thread then waits, at most 5 s, until the connection that
carried it is gone from ``server.active_channels`` (all of it flushed),
and returns 10.
"""
import logging
import signal
import threading
import time

import waitress
from django.conf import settings
from whitenoise import WhiteNoise

from sethlans_manager.body_limits import SETUP_BODY_LIMIT
from sethlans_manager.setup_mode import state
from sethlans_manager.setup_mode.middleware import add_static_headers
from sethlans_manager.setup_mode.views import SAVED_ENVIRON_KEY
from sethlans_manager.waitress_body_limits import limit_request_bodies

logger = logging.getLogger(__name__)

EXIT_STOPPED = 0
EXIT_FAILED = 2
EXIT_SAVED = 10
DRAIN_SECONDS = 5.0
# Exit anyway if the saving response's close never comes (a broken
# client or server path); the settings file is already written.
SAVED_FALLBACK_SECONDS = 20.0
POLL_SECONDS = 0.05


class _ClosingBody:
    def __init__(self, body, on_close):
        self._body = body
        self._on_close = on_close

    def __iter__(self):
        return iter(self._body)

    def close(self):
        try:
            close = getattr(self._body, "close", None)
            if close is not None:
                close()
        finally:
            self._on_close()


class ExitAfterSaved:
    """WSGI wrapper that signals once the response that saved setup is closed."""

    def __init__(self, app):
        self._app = app
        self.saved = threading.Event()
        self.channel = None

    def __call__(self, environ, start_response):
        body = self._app(environ, start_response)
        if not environ.get(SAVED_ENVIRON_KEY):
            return body
        # Waitress puts a bound method of the request's channel here.
        channel = getattr(environ.get("waitress.client_disconnected"), "__self__", None)

        def on_close():
            self.channel = channel
            self.saved.set()

        return _ClosingBody(body, on_close)


def build_app():
    from django.core.wsgi import get_wsgi_application

    app = WhiteNoise(get_wsgi_application(), add_headers_function=add_static_headers)
    frontend = settings.SETUP_FRONTEND_DIR
    if frontend.is_dir():
        app.add_files(str(frontend), prefix="/")
    else:
        logger.warning("The front-end build is missing at %s; /setup/ answers 503", frontend)
    return ExitAfterSaved(app)


def create_server(app, port: int):
    server = waitress.create_server(
        app,
        host="127.0.0.1",
        port=port,
        ident="sethlans-setup",
        threads=4,
        connection_limit=100,
        channel_timeout=60,
        trusted_proxy="127.0.0.1",
        trusted_proxy_count=1,
        trusted_proxy_headers={"x-forwarded-for"},
        clear_untrusted_proxy_headers=True,
        max_request_body_size=SETUP_BODY_LIMIT + 1,
    )
    return limit_request_bodies(server, allow_uploads=False)


def _install_signal_handlers(stop: threading.Event) -> None:
    def handler(signum, _frame):  # pragma: no cover - exercised by a real signal
        logger.info("Signal %s: stopping setup mode", signum)
        stop.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), handler)
            except (ValueError, OSError):  # pragma: no cover - not the main thread
                pass


def drain(server, channel, timeout: float = DRAIN_SECONDS) -> bool:
    """Wait until ``channel`` (or, unknown, every channel) is closed; True when it was."""
    deadline = time.monotonic() + timeout
    while True:
        channels = tuple(server.active_channels.values())
        if not channels or (channel is not None and all(item is not channel for item in channels)):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_SECONDS)


def _serve_forever(server) -> None:
    """The loop thread: an unexpected end is logged, and ``_wait`` sees the thread gone."""
    try:
        server.run()
    except Exception:  # noqa: BLE001 - logged; the main thread returns EXIT_FAILED
        logger.exception("The setup server's loop crashed")


def _wait(wrapper: ExitAfterSaved, stop: threading.Event, thread: threading.Thread) -> int:
    """10 once saved (also when a signal or a dead loop comes after the save),
    0 on a signal before it, 2 when the server loop ends before it."""
    while not wrapper.saved.wait(POLL_SECONDS * 5):
        saved_for = state.STATE.saved_for()
        if stop.is_set():
            if saved_for is None:
                return EXIT_STOPPED
            logger.info("Stop signal after setup was saved; exiting as saved")
            return EXIT_SAVED
        if not thread.is_alive():
            if saved_for is not None:
                return EXIT_SAVED
            logger.error("The setup server stopped serving before setup was saved")
            return EXIT_FAILED
        if saved_for is not None and saved_for > SAVED_FALLBACK_SECONDS:
            logger.warning("The saving response was never closed; exiting anyway")
            return EXIT_SAVED
    return EXIT_SAVED


def run(port: int) -> int:
    """Serve until setup is saved (10) or a signal stops it (0); 2 when it cannot
    start, or when the server loop ends before setup was saved."""
    wrapper = build_app()
    try:
        server = create_server(wrapper, port)
    except (OSError, RuntimeError) as exc:
        logger.error("Setup mode cannot listen on 127.0.0.1:%s: %s", port, exc)
        return EXIT_FAILED
    stop = threading.Event()
    _install_signal_handlers(stop)
    thread = threading.Thread(target=_serve_forever, args=(server,), name="setup-waitress", daemon=True)
    thread.start()
    logger.info("Setup mode listening on http://127.0.0.1:%s/ (Caddy serves HTTPS in front)", port)
    try:
        code = _wait(wrapper, stop, thread)
        if code == EXIT_SAVED and not drain(server, wrapper.channel):
            logger.warning("The saving response's connection was still open after %.0f s", DRAIN_SECONDS)
        return code
    finally:
        # The listener stops; the request threads end. The loop thread is a
        # daemon and may still hold idle proxy connections, which close
        # with the process.
        server.close()
        server.task_dispatcher.shutdown(timeout=2)
