# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wait_for_manager_ready`` and its two call sites.

Covers GitHub issue #94: the launcher previously opened the browser
before uvicorn finished binding the TLS port, so the user landed on a
"can't connect" page on cold-start. The fix polls
``/api/health/`` first and only opens the browser once the manager
responds (or the timeout elapses, in which case we open anyway and
log a warning — graceful degradation).
"""

from __future__ import annotations

import logging
import subprocess
import warnings

import requests
from urllib3.exceptions import InsecureRequestWarning

from launcher import orchestration


# ----------------------------------------------------------------------
# wait_for_manager_ready
# ----------------------------------------------------------------------

class TestWaitForManagerReady:
    """Contract for ``orchestration.wait_for_manager_ready``.

    Polls ``https://127.0.0.1:<port>/api/health/`` at
    ``HEALTH_POLL_INTERVAL`` cadence. Returns True on first 200,
    False on (a) wall-clock timeout, (b) ``manager_proc`` exit, or
    (c) KeyboardInterrupt during the sleep.
    """

    def test_returns_true_on_immediate_200(self, mocker):
        resp = mocker.MagicMock(status_code=200)
        get = mocker.patch.object(
            orchestration.requests, "get", return_value=resp,
        )
        sleep = mocker.patch.object(orchestration.time, "sleep")

        assert orchestration.wait_for_manager_ready(8080) is True
        assert get.call_count == 1
        # Returned before sleeping for the next poll.
        sleep.assert_not_called()

    def test_returns_true_after_connection_errors_then_200(self, mocker):
        # uvicorn-still-binding is signalled by ConnectionError; the
        # function must keep polling and succeed on the eventual 200.
        ok = mocker.MagicMock(status_code=200)
        get = mocker.patch.object(
            orchestration.requests, "get",
            side_effect=[
                requests.exceptions.ConnectionError("refused"),
                requests.exceptions.ConnectionError("refused"),
                requests.exceptions.ConnectionError("refused"),
                requests.exceptions.ConnectionError("refused"),
                ok,
            ],
        )
        mocker.patch.object(orchestration.time, "sleep")

        assert orchestration.wait_for_manager_ready(8080) is True
        assert get.call_count == 5

    def test_returns_true_after_non_200_status_then_200(self, mocker):
        # Non-200 status (e.g. 503 mid-startup) should also be treated
        # as "keep polling". Asserts the loop doesn't bail on first
        # non-200 response.
        bad = mocker.MagicMock(status_code=503)
        ok = mocker.MagicMock(status_code=200)
        get = mocker.patch.object(
            orchestration.requests, "get",
            side_effect=[bad, bad, ok],
        )
        mocker.patch.object(orchestration.time, "sleep")

        assert orchestration.wait_for_manager_ready(8080) is True
        assert get.call_count == 3

    def test_returns_false_on_timeout(self, mocker, caplog):
        mocker.patch.object(
            orchestration.requests, "get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        )
        mocker.patch.object(orchestration.time, "sleep")
        # monotonic sequence: deadline = first call + 30.0, so 31.0
        # crosses the deadline. Sequence: deadline-set, loop check #1,
        # loop check #2 (>= deadline -> False).
        mocker.patch.object(
            orchestration.time, "monotonic",
            side_effect=[0.0, 0.25, 31.0],
        )

        with caplog.at_level(logging.WARNING, logger=orchestration.__name__):
            result = orchestration.wait_for_manager_ready(8080)

        assert result is False
        assert any(
            "manager not ready" in rec.message for rec in caplog.records
        )

    def test_returns_false_when_manager_subprocess_dies(
        self, mocker, caplog,
    ):
        # poll() returns 1 immediately -> we should bail before any
        # network call rather than burn the full 30s timeout.
        proc = mocker.MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 1
        proc.returncode = 1
        get = mocker.patch.object(orchestration.requests, "get")
        mocker.patch.object(orchestration.time, "sleep")

        with caplog.at_level(logging.WARNING, logger=orchestration.__name__):
            result = orchestration.wait_for_manager_ready(
                8080, manager_proc=proc,
            )

        assert result is False
        get.assert_not_called()
        assert any(
            "manager subprocess exited" in rec.message
            for rec in caplog.records
        )

    def test_returns_false_on_keyboard_interrupt_during_sleep(
        self, mocker, caplog,
    ):
        mocker.patch.object(
            orchestration.requests, "get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        )
        mocker.patch.object(
            orchestration.time, "sleep", side_effect=KeyboardInterrupt(),
        )

        with caplog.at_level(logging.WARNING, logger=orchestration.__name__):
            # KI must NOT propagate — the launcher's main loop relies on
            # this returning False so its cleanup path runs.
            result = orchestration.wait_for_manager_ready(8080)

        assert result is False
        assert any(
            "interrupted" in rec.message for rec in caplog.records
        )

    def test_returns_false_on_keyboard_interrupt_during_request(
        self, mocker, caplog,
    ):
        # Ctrl-C can land mid-request (e.g. during the TLS handshake).
        # KI is a BaseException, so the inner ``except RequestException``
        # does NOT catch it — without an outer ``except KeyboardInterrupt``
        # wrapping the whole loop, the function would skip its
        # warning-log + False-return contract and let KI escape.
        mocker.patch.object(
            orchestration.requests, "get", side_effect=KeyboardInterrupt(),
        )
        mocker.patch.object(orchestration.time, "sleep")

        with caplog.at_level(logging.WARNING, logger=orchestration.__name__):
            result = orchestration.wait_for_manager_ready(8080)

        assert result is False
        assert any(
            "interrupted" in rec.message for rec in caplog.records
        )

    def test_uses_verify_false_for_self_signed_cert(self, mocker):
        resp = mocker.MagicMock(status_code=200)
        get = mocker.patch.object(
            orchestration.requests, "get", return_value=resp,
        )
        mocker.patch.object(orchestration.time, "sleep")

        orchestration.wait_for_manager_ready(8080)

        _args, kwargs = get.call_args
        # Self-signed cert means verify must be False or the poll would
        # always fail with SSLError.
        assert kwargs["verify"] is False
        # Per-request timeout must be set or a hung TLS handshake would
        # block the whole poll loop indefinitely.
        assert kwargs["timeout"] == 1

    def test_polls_correct_url(self, mocker):
        resp = mocker.MagicMock(status_code=200)
        get = mocker.patch.object(
            orchestration.requests, "get", return_value=resp,
        )
        mocker.patch.object(orchestration.time, "sleep")

        orchestration.wait_for_manager_ready(8080)

        args, _kwargs = get.call_args
        assert args[0] == "https://127.0.0.1:8080/api/health/"

    def test_suppresses_insecure_request_warning(self, mocker):
        # Simulate the urllib3 warning being raised inside the request
        # call. With warnings filter set to "error" at the test scope,
        # a leaked warning would surface as an exception. The function
        # uses ``warnings.catch_warnings()`` to scope the suppression so
        # the surrounding filter is left untouched on exit.
        def _emit_warning_then_ok(*_args, **_kwargs):
            warnings.warn(
                "Unverified HTTPS request",
                category=InsecureRequestWarning,
            )
            return mocker.MagicMock(status_code=200)

        mocker.patch.object(
            orchestration.requests, "get",
            side_effect=_emit_warning_then_ok,
        )
        mocker.patch.object(orchestration.time, "sleep")

        with warnings.catch_warnings():
            warnings.simplefilter("error", InsecureRequestWarning)
            # Would raise InsecureRequestWarning if the function did not
            # install its own catch_warnings() block.
            assert orchestration.wait_for_manager_ready(8080) is True


# Call-site integration tests live in
# ``test_orchestration_readiness_call_sites.py`` to keep this file
# under the 300-line ceiling (CLAUDE.md).
