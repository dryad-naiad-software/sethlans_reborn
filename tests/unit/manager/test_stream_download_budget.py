# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for issue #114 — streaming downloads had scalar timeouts.

The setup-wizard Blender and FFmpeg downloads both call
``requests.get(..., stream=True, timeout=60)``. With ``stream=True`` a
scalar timeout applies only to individual socket reads, not the overall
transfer — a slow-drip server can keep the call alive forever while
holding a worker thread and a partial archive on disk. Same class of bug
as #113 (parser index fetch), in a different code path.

These tests lock in three guarantees:

1. A hung stream is interrupted by the wall-clock budget timer, the
   partial archive is cleaned up, and the task ends in a terminal
   ``failed`` state — NOT stuck in ``downloading`` forever.
2. Every ``requests.get`` call in both streaming download modules is
   invoked with a tuple ``timeout=`` kwarg (drift guard, mirrors the
   parser-side guard in ``test_blender_series_cache_budget.py``).
3. A user-initiated cancel and a budget-timer cancel are NOT
   indistinguishable to the task state — the error message differs so
   the UI / operator can tell them apart.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from workers.services import (
    blender_download,
    download_budget,
    ffmpeg_download,
)
from workers.services.blender_download import (
    _stream_download as blender_stream_download,
)
from workers.services.download_progress import (
    _download_tasks,
    _tasks_lock,
    create_tagged_task,
    get_task,
)
from workers.services.ffmpeg_download import (
    _stream_download as ffmpeg_stream_download,
)


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_registries():
    """Keep the task registry and budget-timeout sentinel between tests."""
    with _tasks_lock:
        _download_tasks.clear()
    with download_budget._budget_timeouts_lock:
        download_budget._budget_timeouts.clear()
    yield
    with _tasks_lock:
        _download_tasks.clear()
    with download_budget._budget_timeouts_lock:
        download_budget._budget_timeouts.clear()


@pytest.fixture
def tiny_budget(mocker):
    """Patch DOWNLOAD_BUDGET_SECONDS to 0.5s so timer fires quickly."""
    mocker.patch.object(
        download_budget, 'DOWNLOAD_BUDGET_SECONDS', 0.5,
    )


def _hung_response(one_chunk: bytes = b"x" * 1024) -> MagicMock:
    """Return a mock Response whose iter_content drips chunks slowly forever.

    Models a slow-drip CDN: each chunk arrives well within the per-read
    timeout (so the scalar timeout never fires) but the transfer never
    finishes. The only way out is for the cancel event to be tripped by
    something external — in production that's a user click; in these
    tests it's the budget Timer. We drip so the for-loop's cancel guard
    runs between chunks and can observe the event.
    """
    def _iter(chunk_size=65536):
        while True:
            time.sleep(0.05)
            yield one_chunk

    resp = MagicMock()
    resp.headers = {"content-length": str(len(one_chunk) * 1000000)}
    resp.iter_content.side_effect = _iter
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------
# Budget timeout exits the loop
# ---------------------------------------------------------------------

class TestBudgetTimeout:
    """The Timer must set the cancel event when the budget expires;
    ``_stream_download`` must then return False within a bounded time
    and leave the task in a cleanable state for the caller."""

    def test_blender_stream_download_exits_on_budget(
        self, mocker, tmp_path, tiny_budget,
    ):
        task_id, _ = create_tagged_task("blender_")
        mocker.patch(
            'workers.services.blender_download.requests.get',
            return_value=_hung_response(),
        )
        archive = tmp_path / "blender.tar.xz"
        cancel = threading.Event()

        start = time.monotonic()
        result = blender_stream_download(
            "http://example.test/blender.tar.xz",
            archive, task_id, cancel,
        )
        elapsed = time.monotonic() - start

        assert result is False
        assert elapsed < 5.0, (
            f"_stream_download ran for {elapsed:.2f}s; budget 0.5s. "
            "Issue #114 regression — budget timer didn't fire."
        )
        # Budget sentinel recorded for the caller.
        assert download_budget.consume_budget_timeout(task_id) is True

    def test_ffmpeg_stream_download_exits_on_budget(
        self, mocker, tmp_path, tiny_budget,
    ):
        task_id, _ = create_tagged_task("ffmpeg_")
        mocker.patch(
            'workers.services.ffmpeg_download.requests.get',
            return_value=_hung_response(),
        )
        archive = tmp_path / "ffmpeg.zip"
        cancel = threading.Event()

        start = time.monotonic()
        result = ffmpeg_stream_download(
            "http://example.test/ffmpeg.zip",
            archive, task_id, cancel,
        )
        elapsed = time.monotonic() - start

        assert result is False
        assert elapsed < 5.0
        assert download_budget.consume_budget_timeout(task_id) is True


# ---------------------------------------------------------------------
# Full-pipeline budget timeout — partial file cleaned, task terminal
# ---------------------------------------------------------------------

class TestBudgetTimeoutFullPipeline:
    """When the budget fires inside the real pipeline, the caller must
    clean up the partial archive and set the task to a terminal
    ``failed`` state — not leave it stuck at ``downloading``."""

    def test_blender_pipeline_on_budget_cleans_and_fails(
        self, mocker, tmp_path, tiny_budget,
    ):
        task_id, _ = create_tagged_task("blender_")
        mocker.patch(
            'workers.services.blender_download._resolve_release',
            return_value=(
                "http://example.test/blender.tar.xz",
                "00" * 32,  # any non-empty hash passes the presence check
                "linux-x64",
            ),
        )
        mocker.patch(
            'workers.services.blender_download.requests.get',
            return_value=_hung_response(),
        )

        blender_download.download_blender(task_id, tmp_path, "4.5.9")

        progress = get_task(task_id)
        assert progress is not None
        assert progress.status == "failed", (
            "After a budget timeout the task must be in a terminal "
            "state, not stuck at 'downloading'."
        )
        assert "timed out" in (progress.error or "").lower()
        # Archive either never existed or was cleaned up.
        blender_dir = blender_download.get_blender_dir(tmp_path)
        assert not (blender_dir / "blender.tar.xz").exists()

    def test_ffmpeg_pipeline_on_budget_cleans_and_fails(
        self, mocker, tmp_path, tiny_budget,
    ):
        task_id, _ = create_tagged_task("ffmpeg_")
        mocker.patch(
            'workers.services.ffmpeg_download._get_platform_id',
            return_value="linux-x64",
        )
        mocker.patch(
            'workers.services.ffmpeg_download.requests.get',
            return_value=_hung_response(),
        )

        ffmpeg_download.download_ffmpeg(task_id, tmp_path)

        progress = get_task(task_id)
        assert progress is not None
        assert progress.status == "failed"
        assert "timed out" in (progress.error or "").lower()


# ---------------------------------------------------------------------
# User-cancel vs timeout-cancel distinction
# ---------------------------------------------------------------------

class TestCancelVsTimeoutDistinction:
    """Issue #114 contract: a user clicking Cancel and the budget timer
    firing must produce DIFFERENT task-state error strings. If they
    collapsed to the same message, operators couldn't distinguish a
    user action from a stuck CDN in the logs."""

    def test_user_cancel_error_is_not_timeout_error(
        self, mocker, tmp_path, tiny_budget,
    ):
        """User cancel path — pre-set cancel, loop exits on first chunk."""
        task_id, _ = create_tagged_task("blender_")
        mocker.patch(
            'workers.services.blender_download._resolve_release',
            return_value=(
                "http://example.test/blender.tar.xz",
                "00" * 32,
                "linux-x64",
            ),
        )
        resp = MagicMock()
        resp.headers = {"content-length": "1024"}
        # Give the loop a chunk so it enters; the pre-set cancel then
        # breaks out on the cancel check at top of the next iteration.
        resp.iter_content.return_value = [b"x" * 1024] * 4
        resp.raise_for_status = MagicMock()
        mocker.patch(
            'workers.services.blender_download.requests.get',
            return_value=resp,
        )
        # Pre-set the progress task's cancel_event BEFORE download starts.
        progress = get_task(task_id)
        progress.cancel_event.set()

        blender_download.download_blender(task_id, tmp_path, "4.5.9")

        final = get_task(task_id)
        assert final.status == "failed"
        assert final.error == "Cancelled"
        # The budget sentinel must NOT be set on a user cancel.
        assert (
            download_budget.consume_budget_timeout(task_id) is False
        )

    def test_timeout_error_differs_from_user_cancel(
        self, mocker, tmp_path, tiny_budget,
    ):
        """Timeout path — budget fires, error string must be distinct."""
        task_id, _ = create_tagged_task("blender_")
        mocker.patch(
            'workers.services.blender_download._resolve_release',
            return_value=(
                "http://example.test/blender.tar.xz",
                "00" * 32,
                "linux-x64",
            ),
        )
        mocker.patch(
            'workers.services.blender_download.requests.get',
            return_value=_hung_response(),
        )

        blender_download.download_blender(task_id, tmp_path, "4.5.9")

        final = get_task(task_id)
        assert final.status == "failed"
        assert final.error != "Cancelled", (
            "Timeout-cancel must not produce the same error string as "
            "user-cancel — operators need to distinguish them."
        )
        assert "timed out" in final.error.lower()


# ---------------------------------------------------------------------
# Drift guard — timeout= is always a tuple
# ---------------------------------------------------------------------

class TestStreamDownloadAlwaysPassesTupleTimeout:
    """Future drift that replaces the tuple with a scalar re-introduces
    #114. Assert both modules pass a tuple (connect, read) on every
    streaming call. Mirrors the parser-side guard in
    ``test_blender_series_cache_budget.TestParserAlwaysPassesTimeout``."""

    def _assert_tuple_timeout(self, call_kwargs: dict) -> None:
        timeout = call_kwargs.get('timeout')
        assert timeout is not None, (
            "streaming download invoked requests.get without a "
            "timeout kwarg — issue #114 regression."
        )
        assert isinstance(timeout, tuple), (
            f"streaming download passed timeout={timeout!r}; must be a "
            "(connect, read) tuple so a slow-drip stream can't reset "
            "the timer forever. Scalar timeouts caused issue #114."
        )
        assert len(timeout) == 2

    def test_blender_stream_download_passes_tuple_timeout(
        self, mocker, tmp_path,
    ):
        task_id, _ = create_tagged_task("blender_")
        content = b"x" * 1024
        resp = MagicMock()
        resp.headers = {"content-length": str(len(content))}
        resp.iter_content.return_value = [content]
        resp.raise_for_status = MagicMock()
        fake_get = mocker.patch(
            'workers.services.blender_download.requests.get',
            return_value=resp,
        )
        archive = tmp_path / "blender.tar.xz"
        cancel = threading.Event()

        blender_stream_download(
            "http://example.test/blender.tar.xz",
            archive, task_id, cancel,
        )

        assert fake_get.called
        _, kwargs = fake_get.call_args
        self._assert_tuple_timeout(kwargs)

    def test_ffmpeg_stream_download_passes_tuple_timeout(
        self, mocker, tmp_path,
    ):
        task_id, _ = create_tagged_task("ffmpeg_")
        content = b"x" * 1024
        resp = MagicMock()
        resp.headers = {"content-length": str(len(content))}
        resp.iter_content.return_value = [content]
        resp.raise_for_status = MagicMock()
        fake_get = mocker.patch(
            'workers.services.ffmpeg_download.requests.get',
            return_value=resp,
        )
        archive = tmp_path / "ffmpeg.zip"
        cancel = threading.Event()

        ffmpeg_stream_download(
            "http://example.test/ffmpeg.zip",
            archive, task_id, cancel,
        )

        assert fake_get.called
        _, kwargs = fake_get.call_args
        self._assert_tuple_timeout(kwargs)


# ---------------------------------------------------------------------
# Happy path — budget timer must not taint a successful download
# ---------------------------------------------------------------------

class TestHappyPathUnchanged:
    """The budget guard adds a daemon timer that must be cancelled in
    the finally block. If it leaked, a late fire could set the budget
    sentinel for a subsequent task and make it look timed-out.
    Sanity-check that a successful download leaves no sentinel."""

    def test_blender_success_leaves_no_budget_sentinel(
        self, mocker, tmp_path,
    ):
        task_id, _ = create_tagged_task("blender_")
        content = b"x" * 1024
        resp = MagicMock()
        resp.headers = {"content-length": str(len(content))}
        resp.iter_content.return_value = [content]
        resp.raise_for_status = MagicMock()
        mocker.patch(
            'workers.services.blender_download.requests.get',
            return_value=resp,
        )
        archive = tmp_path / "blender.tar.xz"
        cancel = threading.Event()

        result = blender_stream_download(
            "http://example.test/blender.tar.xz",
            archive, task_id, cancel,
        )

        assert result is True
        # No timeout recorded on the happy path.
        assert (
            download_budget.consume_budget_timeout(task_id) is False
        )

    def test_ffmpeg_success_leaves_no_budget_sentinel(
        self, mocker, tmp_path,
    ):
        task_id, _ = create_tagged_task("ffmpeg_")
        content = b"x" * 1024
        resp = MagicMock()
        resp.headers = {"content-length": str(len(content))}
        resp.iter_content.return_value = [content]
        resp.raise_for_status = MagicMock()
        mocker.patch(
            'workers.services.ffmpeg_download.requests.get',
            return_value=resp,
        )
        archive = tmp_path / "ffmpeg.zip"
        cancel = threading.Event()

        result = ffmpeg_stream_download(
            "http://example.test/ffmpeg.zip",
            archive, task_id, cancel,
        )

        assert result is True
        assert (
            download_budget.consume_budget_timeout(task_id) is False
        )


# ---------------------------------------------------------------------
# download_budget module — direct API contract
# ---------------------------------------------------------------------

class TestCancelOrTimeoutError:
    """``cancel_or_timeout_error`` is the single source of truth for
    the user-cancel-vs-timeout error string. Assert the two paths do
    NOT collide and that the sentinel is consumed (clears on read)."""

    def test_default_is_cancelled(self):
        assert (
            download_budget.cancel_or_timeout_error("unknown_task")
            == "Cancelled"
        )

    def test_after_budget_fire_is_timed_out(self):
        cancel = threading.Event()
        download_budget.mark_budget_timeout("tid", cancel)
        msg = download_budget.cancel_or_timeout_error("tid")
        assert msg != "Cancelled"
        assert "timed out" in msg.lower()
        # Second call clears — subsequent reads default back.
        assert (
            download_budget.cancel_or_timeout_error("tid")
            == "Cancelled"
        )

    def test_cancel_event_is_set_by_mark(self):
        cancel = threading.Event()
        assert not cancel.is_set()
        download_budget.mark_budget_timeout("tid", cancel)
        assert cancel.is_set()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
