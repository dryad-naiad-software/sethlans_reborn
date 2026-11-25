# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The setup-mode routes (FR-SM-3). Every API answer is JSON.

* ``GET /api/health/``: ``{"mode": "setup", "boot_id", "last_failure"}``.
* ``POST /api/setup/auth/``: ``{"token"}`` -> 200 ``{"session_token"}`` and
  the session cookie; 403 wrong token; 429 after 10 wrong ones in 60 s.
* ``GET /api/setup/form/``: the pre-filled form (session needed).
* ``POST /api/setup/submit/``: the answers (session needed); 409 while a
  submit runs or after one was saved. The 200 is streamed, so Waitress
  sends it with ``Connection: close`` (a WSGI app may not set that
  header), and ``serve`` exits 10 once it has left.
* ``GET /setup/``: the setup page from the built front end.
"""
import json
import logging
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.decorators.debug import sensitive_variables

from shared.settings_file import SettingsFileError, new_parser, read_settings

from sethlans_manager.setup_mode import auth, leftovers, state
from sethlans_manager.setup_mode.form import build_form
from sethlans_manager.setup_mode.submit import apply_answers
from sethlans_manager.setup_mode.validate import Context

logger = logging.getLogger(__name__)

BOOT_ID = uuid.uuid4().hex
SAVED_ENVIRON_KEY = "sethlans.setup.saved"
_server = {"setup_port": 8080, "reserved_ports": set()}


def configure(setup_port: int, reserved_ports) -> None:
    """The port setup is reached on and the manager's loopback ports (``entry``)."""
    _server["setup_port"] = setup_port
    _server["reserved_ports"] = set(reserved_ports)


def _error(status: int, code: str, message: str, **headers) -> JsonResponse:
    response = JsonResponse({"error": code, "message": message}, status=status)
    for name, value in headers.items():
        response[name.replace("_", "-")] = value
    return response


def _method(request, allowed: str):
    if request.method != allowed:
        return _error(405, "method_not_allowed", f"Use {allowed}.", Allow=allowed)
    return None


def _json_body(request):
    """``(data, None)`` or ``(None, error response)``."""
    if request.content_type != "application/json":
        return None, _error(400, "bad_request", "Send the body as application/json.")
    try:
        return json.loads(request.body.decode("utf-8")), None
    except RequestDataTooBig:
        return None, _error(413, "too_large", "The request body is too large.")
    except (UnicodeDecodeError, ValueError):
        return None, _error(400, "bad_request", "The body is not valid JSON.")


def _request_host(request) -> str:
    """The host name the browser used, without the port (``[v6]`` keeps its brackets)."""
    host = request.get_host()
    if host.startswith("["):
        return host[: host.find("]") + 1]
    return host.split(":", 1)[0]


def _current_settings():
    try:
        return read_settings(), []
    except (SettingsFileError, OSError) as exc:
        return new_parser(), [f"The settings file could not be read: {exc}"]


def health(request):
    return _method(request, "GET") or JsonResponse({
        "mode": "setup",
        "boot_id": BOOT_ID,
        "last_failure": leftovers.last_failure(),
    })


@sensitive_variables("data", "token")
def auth_view(request):
    refused = _method(request, "POST")
    if refused:
        return refused
    data, refused = _json_body(request)
    if refused:
        return refused
    token = data.get("token") if isinstance(data, dict) else None
    client = request.META.get("REMOTE_ADDR") or "unknown"
    outcome = auth.current().attempt(token, client)
    if outcome == auth.LIMITED:
        logger.warning("Setup token attempts from %s are over the limit", client)
        return _error(429, "too_many_attempts", "Too many wrong tokens; wait a minute.", Retry_After="60")
    if outcome != auth.OK:
        logger.warning("Wrong setup token from %s", client)
        return _error(403, "invalid_token", "The setup token is not correct.")
    session = auth.current().sessions.issue()
    logger.info("Setup session opened from %s", client)
    response = JsonResponse({"session_token": session})
    # A browser-session cookie: the 2 h idle expiry is kept on the server.
    response.set_cookie(auth.SESSION_COOKIE, session, path="/", secure=True, httponly=True, samesite="Strict")
    return response


def _session_refused(request):
    if auth.current().session_ok(request):
        return None
    return _error(403, "not_authenticated", "Enter the setup token first.")


def form_view(request):
    refused = _method(request, "GET") or _session_refused(request)
    if refused:
        return refused
    parser, read_errors = _current_settings()
    return JsonResponse(build_form(parser, read_errors))


@sensitive_variables("data")
def submit_view(request):
    refused = _method(request, "POST") or _session_refused(request)
    if refused:
        return refused
    data, refused = _json_body(request)
    if refused:
        return refused
    if not state.STATE.begin():
        return _error(409, "conflict", "Setup is already being saved, or was saved.")
    try:
        parser, read_errors = _current_settings()
        if read_errors:
            state.STATE.reset()
            return JsonResponse({"non_field_errors": read_errors}, status=500)
        ctx = Context(parser, _server["setup_port"], _server["reserved_ports"])
        status, body = apply_answers(data, ctx, _request_host(request))
    except BaseException:
        state.STATE.reset()
        raise
    if status != 200:
        state.STATE.reset()
        return JsonResponse(body, status=status)
    state.STATE.mark_saved()
    request.META[SAVED_ENVIRON_KEY] = True  # the WSGI environ: ``serve`` exits after this response
    return StreamingHttpResponse(iter([json.dumps(body).encode("utf-8")]), content_type="application/json")


def setup_page(request):
    refused = _method(request, "GET")
    if refused:
        return refused
    index = Path(settings.SETUP_FRONTEND_DIR) / "index.html"
    try:
        return HttpResponse(index.read_bytes(), content_type="text/html; charset=utf-8")
    except OSError:
        return HttpResponse("The setup page is not built (manager/frontend).", status=503,
                            content_type="text/plain; charset=utf-8")


def not_found(request, exception=None):
    return _error(404, "not_found", "Not found.")


def server_error(request):
    return _error(500, "server_error", "Setup mode hit an internal error; see the setup log.")
