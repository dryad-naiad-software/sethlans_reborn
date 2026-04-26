# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for FR-W17(a) close()-hook wiring (Phase F1 / CRITICAL-1).

Asserts that the runtime-ready handler:

* Returns a :class:`runtime_ready._ReadyResponseWrapper` for ready
  responses (so Waitress's ``close()`` invocation arms the FR-W17(a)
  grace timer post-flush).
* Returns the bare WSGI body iterable for booting / failed / 4xx
  responses (no wrapper, no close()-hook firing).
* Fires the on-close callback EXACTLY ONCE per ready response, even
  if ``close()`` is called multiple times (transport quirks,
  re-iteration).
* Does NOT fire the callback on cache hits AFTER the first ready
  (FR-W17(a) "first time it returns ready" wording).
"""

from __future__ import annotations

import io
import json

import pytest

from wizard.sethlans_wizard import auth_state, shutdown
from wizard.sethlans_wizard.handlers import runtime_ready
from wizard.sethlans_wizard.handlers.topology import write_topology_atomic


_VALID_SESSION = "valid-session-token-for-close-hook"
_IPC_SECRET = b"close-hook-ipc-secret-bytes-aa"


def _build_environ(method="GET", headers=None):
    env = {
        "REQUEST_METHOD": method,
        "PATH_INFO": "/api/wizard/runtime-ready/",
        "QUERY_STRING": "",
        "REMOTE_ADDR": "127.0.0.1",
        "CONTENT_LENGTH": "0",
        "wsgi.input": io.BytesIO(b""),
    }
    for hk, hv in (headers or {}).items():
        env["HTTP_" + hk.upper().replace("-", "_")] = hv
    return env


def _auth_env():
    return _build_environ(headers={"X-Wizard-Session": _VALID_SESSION})


def _drive(handler, environ):
    """Invoke handler; return (body bytes, response object) for inspection."""
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    response = handler(environ, start_response)
    body = b"".join(response)
    return body, response


@pytest.fixture(autouse=True)
def _reset_state():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(_VALID_SESSION)
    runtime_ready.reset_state_for_tests()
    shutdown.reset_state_for_tests()
    yield
    auth_state.reset_state_for_tests()
    runtime_ready.reset_state_for_tests()
    shutdown.reset_state_for_tests()


def _ok_payload():
    return {"boot_id": "b", "worker_id": "w", "version": "v"}


class TestAckFlagOnlyFromCloseHook:
    """CRITICAL-1 (Phase F1) regression: the FR-W17(a) ack flag MUST
    NOT be set from the handler return path — only from the WSGI
    response iterable's PEP 3333 close() hook."""

    def test_ack_flag_unset_until_close_called(self, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        h = runtime_ready.make_runtime_ready_handler(
            tmp_path, _IPC_SECRET,
            probe_runtime_health=lambda u: _ok_payload(),
            on_first_ready_close=lambda: None,
        )
        # Drive WITHOUT firing close() — simulate body iteration only.
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        response = h(_auth_env(), start_response)
        # Iterate but DO NOT close — production close() is wired via
        # _arm_grace_timer_on_close which mutates auth_state, but the
        # custom on_first_ready_close above is a no-op so the only
        # path to set the flag would be the OLD handler-return code.
        b"".join(response)
        assert auth_state.was_browser_redirect_acknowledged() is False, (
            "ack flag MUST NOT be set from the handler return path "
            "(CRITICAL-1, Phase F1)"
        )

    def test_ack_flag_set_after_close_with_production_callback(
        self, tmp_path, monkeypatch,
    ):
        """The PRODUCTION callback (default) sets the ack via close()."""
        # Replace the grace-timer schedule so we don't arm a real 3s
        # daemon timer that would call os._exit at the test-suite tail.
        monkeypatch.setattr(
            shutdown, "schedule_grace_timer", lambda on_fire=None: True,
        )
        write_topology_atomic(tmp_path, "manager")
        h = runtime_ready.make_runtime_ready_handler(
            tmp_path, _IPC_SECRET,
            probe_runtime_health=lambda u: _ok_payload(),
        )
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        response = h(_auth_env(), start_response)
        b"".join(response)
        # After iteration but before close — ack still False.
        assert auth_state.was_browser_redirect_acknowledged() is False
        response.close()
        # After close — flag transitions True.
        assert auth_state.was_browser_redirect_acknowledged() is True


class TestReadyWrapperShape:

    def test_ready_response_is_wrapped(self, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        h = runtime_ready.make_runtime_ready_handler(
            tmp_path, _IPC_SECRET,
            probe_runtime_health=lambda u: _ok_payload(),
            on_first_ready_close=lambda: None,
        )
        _, response = _drive(h, _auth_env())
        assert isinstance(response, runtime_ready._ReadyResponseWrapper), (
            "ready responses MUST be wrapped to expose close()"
        )

    def test_booting_response_not_wrapped(self, tmp_path):
        # No topology -> booting -> no wrapper.
        h = runtime_ready.make_runtime_ready_handler(
            tmp_path, _IPC_SECRET,
            probe_runtime_health=lambda u: None,
            on_first_ready_close=lambda: None,
        )
        body, response = _drive(h, _auth_env())
        assert not isinstance(response, runtime_ready._ReadyResponseWrapper)
        assert json.loads(body)["status"] == "booting"


class TestCloseHookFires:

    def test_close_runs_callback_exactly_once(self, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        calls: list[int] = []
        h = runtime_ready.make_runtime_ready_handler(
            tmp_path, _IPC_SECRET,
            probe_runtime_health=lambda u: _ok_payload(),
            on_first_ready_close=lambda: calls.append(1),
        )
        _, response = _drive(h, _auth_env())
        # No close() yet — callback MUST NOT fire.
        assert calls == [], "callback fired before close()"
        response.close()
        response.close()
        response.close()
        assert calls == [1], (
            "FR-W17(a) — close()-hook must be idempotent (fired once)"
        )

    def test_close_is_safe_when_callback_raises(self, tmp_path):
        """A buggy callback MUST NOT propagate up through close()."""
        write_topology_atomic(tmp_path, "manager")

        def boom():
            raise RuntimeError("kaboom")

        h = runtime_ready.make_runtime_ready_handler(
            tmp_path, _IPC_SECRET,
            probe_runtime_health=lambda u: _ok_payload(),
            on_first_ready_close=boom,
        )
        _, response = _drive(h, _auth_env())
        # Must not raise.
        response.close()


class TestCacheHitDoesNotRefire:
    """FR-W17(a) — only the FIRST ready response arms the grace timer.

    Subsequent ready responses (same poll cycle, cache hit, second
    browser tab) MUST NOT re-fire the on_first_ready_close callback's
    timer-arming side. The wrapper itself fires close() once per
    response, but the underlying flag-set in the production callback
    is single-shot.
    """

    def test_second_ready_does_not_arm_timer_twice(self, tmp_path, monkeypatch):
        # Replace the grace-timer schedule with a no-op so a real 3s
        # daemon timer doesn't outlive the test (and call os._exit
        # mid-suite).
        arms: list[int] = []

        def fake_schedule(on_fire=None):
            arms.append(1)
            return True

        monkeypatch.setattr(shutdown, "schedule_grace_timer", fake_schedule)

        write_topology_atomic(tmp_path, "manager")
        # Use the production callback so we exercise the
        # mark_browser_redirect_acknowledged() idempotency.
        h = runtime_ready.make_runtime_ready_handler(
            tmp_path, _IPC_SECRET,
            probe_runtime_health=lambda u: _ok_payload(),
        )
        # First call — should call schedule_grace_timer once.
        _, r1 = _drive(h, _auth_env())
        r1.close()
        assert arms == [1], "first close MUST arm the timer"
        # Second call — ack is already True so the production
        # callback returns early; schedule MUST NOT be called again.
        _, r2 = _drive(h, _auth_env())
        r2.close()
        assert arms == [1], (
            "second close() MUST NOT re-arm the grace timer"
        )


class TestDoneSignal:
    """The done handler MUST signal mark_done_written so the post-done
    polling thread can start."""

    def test_done_handler_marks_done_written(self, tmp_path):
        # Build the done-handler's collaborators inline.
        from wizard.sethlans_wizard.handlers.done import make_done_handler
        from wizard.sethlans_wizard.handlers.topology import (
            write_topology_atomic,
        )

        write_topology_atomic(tmp_path, "worker_only")
        h = make_done_handler(
            tmp_path, _IPC_SECRET, wizard_port=8101,
        )
        env = _build_environ(
            method="POST",
            headers={"X-Wizard-Session": _VALID_SESSION},
        )
        env["wsgi.input"] = io.BytesIO(b"{}")
        env["CONTENT_LENGTH"] = "2"

        assert shutdown.done_written.is_set() is False
        _drive(h, env)
        assert shutdown.done_written.is_set() is True, (
            "done handler MUST call shutdown.mark_done_written"
        )
