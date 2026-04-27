# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for ``file_operations.download_file`` (#115).

Locks in the timeout + wall-clock budget + partial-cleanup behavior
mirroring the manager-side fix in #113 / #114. Before this fix the
helper called ``requests.get(url, stream=True)`` with no ``timeout=``
kwarg at all, so a hung server would pin a worker thread forever.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from sethlans_worker_agent.utils import file_operations


# ---- Helpers ----------------------------------------------------------------


def _make_response(chunks: list[bytes], content_length: int | None = None):
    """Build a context-manager-shaped mock that mimics ``requests.Response``.

    The real return value of ``requests.get(stream=True)`` is a Response
    that is also a context manager (``__enter__`` returns itself,
    ``__exit__`` is a no-op). The download code path consumes
    ``raise_for_status()``, ``headers.get("content-length", 0)``, and
    ``iter_content(chunk_size=...)``.
    """
    resp = MagicMock(spec=requests.Response)
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    resp.headers = {
        "content-length": str(
            content_length if content_length is not None
            else sum(len(c) for c in chunks)
        ),
    }
    resp.raise_for_status.return_value = None
    resp.iter_content.return_value = iter(chunks)
    return resp


# ---- Drift guards -----------------------------------------------------------


class TestRequestsGetTimeoutKwarg:
    """``download_file`` must always pass ``timeout=HTTP_TIMEOUT``."""

    def test_bare_requests_path_passes_timeout(
        self, mocker, tmp_path,
    ) -> None:
        fake_get = mocker.patch.object(file_operations.requests, "get")
        fake_get.return_value = _make_response([b"hello"])

        file_operations.download_file(
            "https://example.com/file.txt", str(tmp_path),
        )

        kwargs = fake_get.call_args.kwargs
        assert kwargs.get("timeout") == file_operations.HTTP_TIMEOUT, (
            "download_file must pass timeout=HTTP_TIMEOUT to "
            "requests.get to bound connect/read stalls (#115)."
        )

    def test_session_path_passes_timeout(self, mocker, tmp_path) -> None:
        """When a session is provided, the same timeout kwarg flows through."""
        session = MagicMock()
        session.get.return_value = _make_response([b"hello"])

        file_operations.download_file(
            "https://example.com/file.txt", str(tmp_path), session=session,
        )

        kwargs = session.get.call_args.kwargs
        assert kwargs.get("timeout") == file_operations.HTTP_TIMEOUT, (
            "download_file must pass timeout=HTTP_TIMEOUT to "
            "session.get too (#115). The pinning manager session is "
            "the highest-traffic call site."
        )


class TestHttpTimeoutShape:
    """``HTTP_TIMEOUT`` is a ``(connect, read)`` tuple matching #113/#114."""

    def test_module_constant_is_connect_read_tuple(self) -> None:
        t = file_operations.HTTP_TIMEOUT
        assert isinstance(t, tuple) and len(t) == 2, (
            "HTTP_TIMEOUT must be a (connect, read) tuple — bare-int "
            "form would apply only to connect, leaving read unbounded."
        )
        connect, read = t
        assert isinstance(connect, (int, float)) and connect > 0
        assert isinstance(read, (int, float)) and read > 0


# ---- Wall-clock budget ------------------------------------------------------


class TestWallClockBudget:
    """A download exceeding ``STALL_BUDGET_SECONDS`` aborts mid-stream."""

    def test_budget_exceeded_raises_timeout_error(
        self, mocker, tmp_path,
    ) -> None:
        # Negative budget → deadline is in the past at first iteration,
        # so the wall-clock check fires immediately. Deterministic, no
        # actual sleeping, no race risk on slow CI.
        mocker.patch.object(
            file_operations, "STALL_BUDGET_SECONDS", -1,
        )
        fake_get = mocker.patch.object(file_operations.requests, "get")
        fake_get.return_value = _make_response(
            [b"chunk1", b"chunk2", b"chunk3"],
        )

        with pytest.raises(TimeoutError, match="exceeded"):
            file_operations.download_file(
                "https://example.com/file.txt", str(tmp_path),
            )

    def test_budget_exceeded_removes_partial_file(
        self, mocker, tmp_path,
    ) -> None:
        mocker.patch.object(
            file_operations, "STALL_BUDGET_SECONDS", -1,
        )
        fake_get = mocker.patch.object(file_operations.requests, "get")
        fake_get.return_value = _make_response(
            [b"chunk1", b"chunk2"],
        )

        with pytest.raises(TimeoutError):
            file_operations.download_file(
                "https://example.com/file.txt", str(tmp_path),
            )

        assert list(tmp_path.iterdir()) == [], (
            "Partial download file must be removed on wall-clock "
            "abort (#115). Leftover junk on disk would confuse the "
            "next caller's hash check."
        )


# ---- Streaming-error cleanup ------------------------------------------------


class TestPartialFileCleanupOnStreamError:
    """Streaming exceptions trigger partial-file cleanup."""

    def test_connection_dropped_mid_stream_removes_partial(
        self, mocker, tmp_path,
    ) -> None:
        fake_get = mocker.patch.object(file_operations.requests, "get")

        def iter_then_die():
            yield b"first chunk"
            raise requests.exceptions.ChunkedEncodingError(
                "connection died"
            )

        resp = MagicMock(spec=requests.Response)
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        resp.headers = {"content-length": "100"}
        resp.raise_for_status.return_value = None
        resp.iter_content.return_value = iter_then_die()
        fake_get.return_value = resp

        with pytest.raises(requests.exceptions.ChunkedEncodingError):
            file_operations.download_file(
                "https://example.com/file.txt", str(tmp_path),
            )

        assert list(tmp_path.iterdir()) == [], (
            "Partial download must be removed when streaming "
            "raises ChunkedEncodingError (#115)."
        )

    def test_http_error_does_not_leave_empty_file(
        self, mocker, tmp_path,
    ) -> None:
        """``raise_for_status`` failure must not leave a 0-byte stub."""
        fake_get = mocker.patch.object(file_operations.requests, "get")
        resp = MagicMock(spec=requests.Response)
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        resp.headers = {"content-length": "0"}
        resp.raise_for_status.side_effect = (
            requests.exceptions.HTTPError("404")
        )
        fake_get.return_value = resp

        with pytest.raises(requests.exceptions.HTTPError):
            file_operations.download_file(
                "https://example.com/missing.txt", str(tmp_path),
            )

        assert list(tmp_path.iterdir()) == [], (
            "An HTTP error before any bytes stream must not leave "
            "a 0-byte stub on disk (#115)."
        )


# ---- Happy path -------------------------------------------------------------


class TestSuccessfulDownload:
    """Sanity baseline: a normal download still works end-to-end."""

    def test_writes_bytes_and_returns_path(self, mocker, tmp_path) -> None:
        chunks = [b"hello ", b"world"]
        fake_get = mocker.patch.object(file_operations.requests, "get")
        fake_get.return_value = _make_response(chunks)

        result = file_operations.download_file(
            "https://example.com/greeting.txt", str(tmp_path),
        )

        expected = tmp_path / "greeting.txt"
        assert result == str(expected)
        assert expected.read_bytes() == b"hello world"
