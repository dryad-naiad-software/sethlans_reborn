# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
In-process re-entrancy guard + closed-vocabulary error assertions
for the FFmpeg part.

Companion to ``test_parts_check_ffmpeg.py`` (the check_ffmpeg flow
tests).  These tests are split out to keep individual files under
the project's 300-line cap.

Per spec:
- FR §30 / §178-180: a second concurrent ``run_parts_check`` caller
  must NOT spawn a duplicate check; the in-flight status is what
  readers see during the boot window.
- FR §70-79: the ``error`` field on a failed status is drawn from a
  fixed closed vocabulary; it never contains raw paths, exception
  messages, subprocess stderr, or other free-form text.
"""

import threading
import time

import pytest

from workers.services.parts_check import ffmpeg as ffmpeg_part
from workers.services.parts_check import registry


CLOSED_VOCAB_ERRORS = {
    "download_failed",
    "checksum_mismatch",
    "extraction_unsafe",
    "extraction_failed",
    "verify_runs_failed",
    "version_below_8",
    "override_path_invalid",
    "override_path_unverifiable",
    "placeholder_sha",
}


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset registry state between tests."""
    registry._reset_for_tests()
    yield
    registry._reset_for_tests()


@pytest.fixture
def isolate_ffmpeg(tmp_path, mocker):
    """Isolate check_ffmpeg from real filesystem and config."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mocker.patch.object(ffmpeg_part, "get_data_dir", return_value=data_dir)
    override_reader = mocker.patch.object(
        ffmpeg_part, "_read_manager_ini_override", return_value=None,
    )
    mocker.patch.object(ffmpeg_part, "cleanup_stale_partials")
    return {"override_reader": override_reader}


# ---- In-process re-entrancy guard -----------------------------------

def test_in_process_re_entrancy_guard_no_duplicate_check():
    """Concurrent run_parts_check calls fire the check at most once.

    The ``_thread_started`` guard inside the registry ensures a
    second concurrent caller does NOT spawn a duplicate check.  This
    matches FR §30: an in-flight reader sees the in-flight status
    without firing a second check.
    """
    call_count = []
    barrier = threading.Event()

    def slow_check():
        call_count.append(1)
        barrier.wait(timeout=2.0)
        return registry.Status(status="ready", source="system")

    registry.register_part("ffmpeg", slow_check)
    registry.run_parts_check()
    # Second/third concurrent callers (e.g. a post-save signal that
    # asks for in-flight status) must NOT spawn a duplicate thread.
    registry.run_parts_check()
    registry.run_parts_check()

    # Wait for the single check_fn to enter.
    for _ in range(50):
        if call_count:
            break
        time.sleep(0.01)

    # During the in-flight window, status reads return the live
    # snapshot (initial "installing") without firing another check.
    in_flight = registry.get_status("ffmpeg")
    assert in_flight.status == "installing"

    barrier.set()
    for _ in range(100):
        if registry.get_status("ffmpeg").status == "ready":
            break
        time.sleep(0.01)

    assert len(call_count) == 1


# ---- Closed-vocabulary error string assertions ----------------------

def test_override_invalid_error_is_exact_closed_vocab_string(
    isolate_ffmpeg,
):
    """error must be exactly 'override_path_invalid', no extra text."""
    isolate_ffmpeg["override_reader"].return_value = "/nonexistent/x"
    status = ffmpeg_part.check_ffmpeg()
    assert status.error == "override_path_invalid"
    assert status.error in CLOSED_VOCAB_ERRORS


def test_override_unverifiable_error_is_exact_closed_vocab_string(
    isolate_ffmpeg, mocker, tmp_path,
):
    """error must be exactly 'override_path_unverifiable'."""
    fake_binary = tmp_path / "ffmpeg"
    fake_binary.write_bytes(b"\x7fELF")
    isolate_ffmpeg["override_reader"].return_value = str(fake_binary)
    mocker.patch.object(ffmpeg_part, "verify_runs", return_value=False)

    status = ffmpeg_part.check_ffmpeg()
    assert status.error == "override_path_unverifiable"
    assert status.error in CLOSED_VOCAB_ERRORS


def test_failed_status_error_never_contains_freeform_text(
    isolate_ffmpeg,
):
    """error field never leaks paths, exception messages, or stderr.

    Regression guard: the override invalid path includes a unique
    sentinel string we can grep for.  If that sentinel ever ends up
    in ``status.error``, the implementation has leaked free-form
    text into the closed-vocabulary field.
    """
    sentinel_path = "/wibble-wobble-leak-canary-zzz/ffmpeg"
    isolate_ffmpeg["override_reader"].return_value = sentinel_path
    status = ffmpeg_part.check_ffmpeg()
    assert "wibble" not in (status.error or "")
    assert "wobble" not in (status.error or "")
    assert "leak-canary" not in (status.error or "")


def test_failed_status_error_is_short_token(isolate_ffmpeg):
    """A failed status's error string is a short single-token code.

    Defense against an implementation that might prepend or append
    free-form context.  The closed-vocab tokens are at most ~30 chars.
    """
    isolate_ffmpeg["override_reader"].return_value = "/nope"
    status = ffmpeg_part.check_ffmpeg()
    assert status.status == "failed"
    assert status.error is not None
    assert len(status.error) <= 30
    # Token: alphanumeric and underscores only.
    assert all(c.isalnum() or c == "_" for c in status.error)
