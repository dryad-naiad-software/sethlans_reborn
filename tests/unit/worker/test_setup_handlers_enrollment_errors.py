# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/setup/handlers_enrollment.py`` -- error-path
and invariant coverage.

Companion to ``test_setup_handlers_enrollment.py``.  This file covers:

* ``enrollment_client`` error -> HTTP status mapping (403 / 429 / 502).
* ``setup_mutation_lock`` contention -> 409 Conflict.
* FR-6 timeout invariant: the outbound enrollment POST carries
  ``timeout=15``, asserted at the ``requests`` boundary so a future
  refactor that strips the kwarg is caught by a test failure rather
  than a hanging Waitress worker thread.

Split from the main handler test file to respect the 300-line Python
ceiling (same pattern as the ``_concurrency`` split on discovery).
"""

import io
import json
import threading

from sethlans_worker_agent.web_ui.setup import handlers_discovery
from sethlans_worker_agent.web_ui.setup import lock as lock_module
from sethlans_worker_agent.web_ui.setup.handlers_enrollment import (
    handle_enroll,
)

from tests.unit.worker._wsgi_helpers import StartResponseCapture


# -------------------------------------------------------------------
# Helpers (mirrored from the main test file -- tiny enough to keep
# each split file self-contained; matches the discovery split
# pattern).
# -------------------------------------------------------------------


def _make_environ(body: bytes = b'') -> dict:
    env = {
        'REQUEST_METHOD': 'POST',
        'PATH_INFO': '/api/setup/worker/enroll/',
        'wsgi.input': io.BytesIO(body),
    }
    if body:
        env['CONTENT_LENGTH'] = str(len(body))
    return env


def _call(handler, environ, cap):
    return b''.join(handler(environ, cap))


def _status_code(cap: StartResponseCapture) -> int:
    return int((cap.status or '').split(' ', 1)[0])


def _set_selected_manager() -> None:
    """Pre-populate the selected-manager module state."""
    handlers_discovery._selected_manager_url = (
        "https://10.0.0.1:8080/api/"
    )
    handlers_discovery._selected_manager_id = "mgr-id-42"
    handlers_discovery._selected_manager_meta = {
        "name": "Test Manager",
        "ip": "10.0.0.1",
        "host": "lab.example",
        "port": 8080,
    }


# -------------------------------------------------------------------
# enrollment_client error mapping
# -------------------------------------------------------------------


class TestHandleEnrollErrorMapping:
    def test_returns_403_for_invalid_key(self, mocker):
        _set_selected_manager()
        from sethlans_worker_agent import enrollment_client

        mocker.patch(
            "sethlans_worker_agent.enrollment_client.enroll",
            side_effect=enrollment_client.InvalidKeyError(
                "Invalid key"
            ),
        )

        body = json.dumps({
            "enrollment_key": "BADKEY1234567890",
        }).encode()
        cap = StartResponseCapture()
        out = _call(handle_enroll, _make_environ(body), cap)

        assert _status_code(cap) == 403
        assert "Invalid key" in json.loads(out)["error"]

    def test_returns_429_for_rate_limited(self, mocker):
        _set_selected_manager()
        from sethlans_worker_agent import enrollment_client

        mocker.patch(
            "sethlans_worker_agent.enrollment_client.enroll",
            side_effect=enrollment_client.RateLimitedError(
                "Too many attempts"
            ),
        )

        body = json.dumps({
            "enrollment_key": "4F9XK2PBQ7M3N8RT",
        }).encode()
        cap = StartResponseCapture()
        out = _call(handle_enroll, _make_environ(body), cap)

        assert _status_code(cap) == 429
        assert "Too many attempts" in json.loads(out)["error"]

    def test_returns_502_for_generic_enrollment_error(self, mocker):
        _set_selected_manager()
        from sethlans_worker_agent import enrollment_client

        mocker.patch(
            "sethlans_worker_agent.enrollment_client.enroll",
            side_effect=enrollment_client.EnrollmentError(
                "Connection refused"
            ),
        )

        body = json.dumps({
            "enrollment_key": "4F9XK2PBQ7M3N8RT",
        }).encode()
        cap = StartResponseCapture()
        out = _call(handle_enroll, _make_environ(body), cap)

        assert _status_code(cap) == 502
        assert "Connection refused" in json.loads(out)["error"]


# -------------------------------------------------------------------
# Concurrency (setup_mutation_lock -> 409)
# -------------------------------------------------------------------


class TestHandleEnrollContention:
    def test_returns_409_when_mutation_lock_held(self):
        """Hold ``setup_mutation_lock`` in a background thread, then
        POST /enroll/ -- must 409 with the canonical error shape.
        Matches the pattern used by ``handle_select_manager``.
        """
        _set_selected_manager()
        lock_held = threading.Event()
        release_holder = threading.Event()

        def holder():
            with lock_module.setup_mutation_lock() as acquired:
                assert acquired
                lock_held.set()
                release_holder.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        try:
            assert lock_held.wait(timeout=5)

            body = json.dumps({
                "enrollment_key": "4F9XK2PBQ7M3N8RT",
            }).encode()
            cap = StartResponseCapture()
            out = _call(handle_enroll, _make_environ(body), cap)

            assert _status_code(cap) == 409
            assert json.loads(out) == {
                "error": "Setup mutation in progress; retry after "
                         "current operation completes.",
            }
        finally:
            release_holder.set()
            t.join(timeout=5)


# -------------------------------------------------------------------
# FR-6 timeout invariant
# -------------------------------------------------------------------


class TestTimeoutInvariant:
    """Lock in the explicit ``timeout=15`` on the outbound HTTP call.

    The sync rewrite inlined the executor hop; the bounded ``timeout``
    on ``Session.post`` is now the ONLY thing keeping a hung manager
    from pinning a Waitress worker thread indefinitely.  Assert it at
    the ``requests`` boundary so a future refactor that drops the
    kwarg is caught here rather than in production.
    """

    def test_enroll_passes_timeout_15_to_requests_post(self, mocker):
        _set_selected_manager()

        # Mock the underlying requests.Session so we can inspect the
        # timeout kwarg without mocking enrollment_client itself.
        # We don't care about the envelope parsing for this test --
        # let enrollment_client raise after the POST and assert the
        # call was made.
        mock_session = mocker.MagicMock()
        mock_response = mocker.MagicMock()
        mock_response.status_code = 503  # -> ServiceUnavailableError
        mock_response.text = "nope"
        mock_session.post.return_value = mock_response
        mocker.patch(
            "sethlans_worker_agent.enrollment_client.get_session",
            return_value=mock_session,
        )
        mocker.patch(
            "sethlans_worker_agent.config_store.set_many",
        )

        body = json.dumps({
            "enrollment_key": "4F9XK2PBQ7M3N8RT",
        }).encode()
        cap = StartResponseCapture()
        _call(handle_enroll, _make_environ(body), cap)

        assert mock_session.post.called, (
            "enrollment never reached the requests layer -- test is "
            "not actually exercising the timeout invariant"
        )
        _, kwargs = mock_session.post.call_args
        assert kwargs.get("timeout") == 15, (
            f"FR-6 violation: outbound enrollment POST was invoked "
            f"without timeout=15 (got {kwargs.get('timeout')!r}). "
            f"Waitress worker threads could hang indefinitely."
        )
