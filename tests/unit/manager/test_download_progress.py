# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``manager/workers/services/download_progress.py``.

Covers task lifecycle (create, update, get, remove), tagged tasks,
active-task discovery, cancel event propagation, and immutable
replacement semantics.
"""

import threading

import pytest

from workers.services.download_progress import (
    DownloadProgress,
    _download_tasks,
    _tasks_lock,
    create_tagged_task,
    create_task,
    find_active_task,
    get_task,
    remove_task,
    update_task,
)


@pytest.fixture(autouse=True)
def _clean_task_registry():
    """Clear the module-level task registry before/after each test."""
    with _tasks_lock:
        _download_tasks.clear()
    yield
    with _tasks_lock:
        _download_tasks.clear()


# ---- create_task() -----------------------------------------------------------

class TestCreateTask:

    def test_returns_tuple(self):
        task_id, progress = create_task()
        assert isinstance(task_id, str)
        assert isinstance(progress, DownloadProgress)

    def test_task_is_registered(self):
        task_id, _ = create_task()
        assert get_task(task_id) is not None

    def test_initial_state_is_pending(self):
        _, progress = create_task()
        assert progress.status == "pending"
        assert progress.percent == 0
        assert progress.error is None

    def test_task_id_is_unique(self):
        ids = {create_task()[0] for _ in range(50)}
        assert len(ids) == 50


# ---- get_task() --------------------------------------------------------------

class TestGetTask:

    def test_returns_none_for_unknown(self):
        assert get_task("nonexistent") is None

    def test_returns_current_progress(self):
        task_id, _ = create_task()
        update_task(task_id, status="downloading", percent=42)
        progress = get_task(task_id)
        assert progress.status == "downloading"
        assert progress.percent == 42


# ---- update_task() -----------------------------------------------------------

class TestUpdateTask:

    def test_replaces_progress_immutably(self):
        task_id, original = create_task()
        update_task(task_id, status="downloading", percent=50)
        updated = get_task(task_id)
        assert updated is not original
        assert updated.status == "downloading"
        assert updated.percent == 50

    def test_preserves_cancel_event(self):
        task_id, original = create_task()
        cancel = original.cancel_event
        update_task(task_id, status="extracting")
        updated = get_task(task_id)
        assert updated.cancel_event is cancel

    def test_noop_for_unknown_task(self):
        """Updating a nonexistent task does nothing."""
        update_task("nonexistent", status="failed")
        assert get_task("nonexistent") is None


# ---- remove_task() -----------------------------------------------------------

class TestRemoveTask:

    def test_removes_existing(self):
        task_id, _ = create_task()
        remove_task(task_id)
        assert get_task(task_id) is None

    def test_noop_for_unknown(self):
        """Removing nonexistent task does not raise."""
        remove_task("nonexistent")


# ---- create_tagged_task() ----------------------------------------------------

class TestCreateTaggedTask:

    def test_id_starts_with_tag(self):
        task_id, _ = create_tagged_task("ffmpeg_")
        assert task_id.startswith("ffmpeg_")

    def test_task_is_registered(self):
        task_id, _ = create_tagged_task("blender_")
        assert get_task(task_id) is not None


# ---- find_active_task() ------------------------------------------------------

class TestFindActiveTask:

    def test_finds_matching_active_task(self):
        task_id, _ = create_tagged_task("ffmpeg_")
        result = find_active_task("ffmpeg_")
        assert result is not None
        assert result[0] == task_id

    def test_skips_completed_tasks(self):
        task_id, _ = create_tagged_task("ffmpeg_")
        update_task(task_id, status="complete", percent=100)
        assert find_active_task("ffmpeg_") is None

    def test_skips_failed_tasks(self):
        task_id, _ = create_tagged_task("ffmpeg_")
        update_task(task_id, status="failed", error="oops")
        assert find_active_task("ffmpeg_") is None

    def test_returns_none_when_no_match(self):
        create_tagged_task("blender_")
        assert find_active_task("ffmpeg_") is None

    @pytest.mark.parametrize("status", [
        "pending", "downloading", "extracting", "verifying",
    ])
    def test_finds_tasks_in_active_statuses(self, status):
        task_id, _ = create_tagged_task("ffmpeg_")
        update_task(task_id, status=status)
        result = find_active_task("ffmpeg_")
        assert result is not None


# ---- Cancel event propagation ------------------------------------------------

class TestCancelEvent:

    def test_cancel_event_is_threading_event(self):
        _, progress = create_task()
        assert isinstance(progress.cancel_event, threading.Event)

    def test_cancel_event_shared_through_updates(self):
        task_id, original = create_task()
        event = original.cancel_event
        update_task(task_id, status="downloading")
        updated = get_task(task_id)
        assert updated.cancel_event is event

    def test_setting_cancel_visible_after_update(self):
        task_id, original = create_task()
        original.cancel_event.set()
        update_task(task_id, status="downloading")
        updated = get_task(task_id)
        assert updated.cancel_event.is_set()


# ---- DownloadProgress frozen dataclass ----------------------------------------

class TestDownloadProgressDataclass:

    def test_frozen_prevents_mutation(self):
        prog = DownloadProgress()
        with pytest.raises(AttributeError):
            prog.status = "changed"

    def test_defaults(self):
        prog = DownloadProgress()
        assert prog.status == "pending"
        assert prog.percent == 0
        assert prog.error is None
