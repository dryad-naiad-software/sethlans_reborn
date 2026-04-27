# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared helpers for the runtime-ready handler unit tests.

Phase F4 split ``test_handlers_runtime_ready.py`` into two files for
the 300-line limit; these helpers are imported by both halves.
"""

from __future__ import annotations

import io
import json

from wizard.sethlans_wizard import auth_state
from wizard.sethlans_wizard.handlers import runtime_ready


VALID_SESSION = "valid-session-token-for-rr"
IPC_SECRET = b"ipc-hmac-secret-bytes-rr-xxx"


def build_environ(*, method="GET", path="/api/wizard/runtime-ready/",
                  remote_addr="127.0.0.1", query_string="", headers=None):
    env = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "REMOTE_ADDR": remote_addr,
        "CONTENT_LENGTH": "0",
        "wsgi.input": io.BytesIO(b""),
    }
    for hk, hv in (headers or {}).items():
        env["HTTP_" + hk.upper().replace("-", "_")] = hv
    return env


def call(handler, environ, *, fire_close=True):
    """Invoke *handler* and (by default) drive the PEP 3333 close() hook.

    Waitress invokes ``close()`` after the response body has been
    flushed to the wire. Production behaviour for ready responses
    depends on this hook firing, so the test harness mirrors it.
    Tests that want to inspect "what would happen if close() never
    fires" pass ``fire_close=False``.
    """
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    response = handler(environ, start_response)
    body = b"".join(response)
    if fire_close:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    parsed = json.loads(body.decode("utf-8")) if body else None
    return captured["status"], dict(captured["headers"]), parsed


def silent_close():
    """Default ``on_first_ready_close`` for tests — no real shutdown."""
    auth_state.mark_browser_redirect_acknowledged()


def auth_env(**kw):
    headers = kw.pop("headers", {}) or {}
    headers["X-Wizard-Session"] = VALID_SESSION
    return build_environ(headers=headers, **kw)


def ok_payload():
    return {"boot_id": "x", "worker_id": "y", "version": "1"}


def make_handler(data_dir, probe_fn, *, on_first_ready_close=None):
    return runtime_ready.make_runtime_ready_handler(
        data_dir, IPC_SECRET, probe_runtime_health=probe_fn,
        on_first_ready_close=on_first_ready_close or silent_close,
    )
