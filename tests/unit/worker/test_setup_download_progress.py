# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/setup/download_progress.py``.

Covers task creation, retrieval, update, removal, and
``find_active_task`` filtering.  Module-level ``_download_tasks``
is reset by the autouse fixture in ``conftest.py``.
"""

import threading

from sethlans_worker_agent.web_ui.setup.download_progress import (
    DownloadProgress,
    create_task,
    create_tagged_task,
    get_task,
    update_task,
    remove_task,
    find_active_task,
)


# -------------------------------------------------------------------
# create_task
# -------------------------------------------------------------------

class TestCreateTask:
    def test_returns_tuple_with_task_id_and_progress(self):
        task_id, progress = create_task()
        assert isinstance(task_id, str)
        assert len(task_id) > 0
        assert isinstance(progress, DownloadProgress)

    def test_default_status_is_pending(self):
        _, progress = create_task()
        assert progress.status == "pending"
        assert progress.percent == 0
        assert progress.error is None

    def test_cancel_event_is_threading_event(self):
        _, progress = create_task()
        assert isinstance(progress.cancel_event, threading.Event)
        assert not progress.cancel_event.is_set()


# -------------------------------------------------------------------
# create_tagged_task
# -------------------------------------------------------------------

class TestCreateTaggedTask:
    def test_prefixes_task_id_with_tag(self):
        task_id, _ = create_tagged_task("blender_")
        assert task_id.startswith("blender_")

    def test_tag_preserved_in_registry(self):
        task_id, _ = create_tagged_task("blender_")
        found = get_task(task_id)
        assert found is not None


# -------------------------------------------------------------------
# get_task
# -------------------------------------------------------------------

class TestGetTask:
    def test_returns_progress_for_existing_task(self):
        task_id, original = create_task()
        result = get_task(task_id)
        assert result is original

    def test_returns_none_for_missing_task(self):
        assert get_task("nonexistent-id") is None


# -------------------------------------------------------------------
# update_task
# -------------------------------------------------------------------

class TestUpdateTask:
    def test_replaces_entry(self):
        task_id, _ = create_task()
        update_task(task_id, status="downloading", percent=50)
        result = get_task(task_id)
        assert result.status == "downloading"
        assert result.percent == 50

    def test_preserves_cancel_event(self):
        task_id, original = create_task()
        original_event = original.cancel_event
        update_task(task_id, status="downloading", percent=25)
        result = get_task(task_id)
        assert result.cancel_event is original_event

    def test_allows_explicit_cancel_event_override(self):
        task_id, _ = create_task()
        new_event = threading.Event()
        update_task(
            task_id, status="downloading",
            cancel_event=new_event,
        )
        result = get_task(task_id)
        assert result.cancel_event is new_event

    def test_noop_for_missing_task(self):
        # Should not raise.
        update_task("nonexistent", status="downloading")
        assert get_task("nonexistent") is None


# -------------------------------------------------------------------
# remove_task
# -------------------------------------------------------------------

class TestRemoveTask:
    def test_removes_existing_task(self):
        task_id, _ = create_task()
        remove_task(task_id)
        assert get_task(task_id) is None

    def test_noop_for_missing_task(self):
        # Should not raise.
        remove_task("nonexistent")


# -------------------------------------------------------------------
# find_active_task
# -------------------------------------------------------------------

class TestFindActiveTask:
    def test_finds_pending_task_by_prefix(self):
        task_id, _ = create_tagged_task("blender_")
        result = find_active_task("blender_")
        assert result is not None
        assert result[0] == task_id
        assert result[1].status == "pending"

    def test_finds_downloading_task(self):
        task_id, _ = create_tagged_task("blender_")
        update_task(task_id, status="downloading", percent=42)
        result = find_active_task("blender_")
        assert result is not None
        assert result[1].status == "downloading"

    def test_ignores_completed_task(self):
        task_id, _ = create_tagged_task("blender_")
        update_task(task_id, status="complete", percent=100)
        assert find_active_task("blender_") is None

    def test_ignores_failed_task(self):
        task_id, _ = create_tagged_task("blender_")
        update_task(
            task_id, status="failed", error="network error",
        )
        assert find_active_task("blender_") is None

    def test_returns_none_when_no_prefix_match(self):
        create_tagged_task("other_")
        assert find_active_task("blender_") is None

    def test_returns_none_when_empty(self):
        assert find_active_task("blender_") is None


# -------------------------------------------------------------------
# DownloadProgress frozen dataclass
# -------------------------------------------------------------------

class TestDownloadProgressDataclass:
    def test_is_frozen(self):
        p = DownloadProgress()
        with __import__("pytest").raises(AttributeError):
            p.status = "downloading"  # type: ignore[misc]

    def test_equality_ignores_cancel_event(self):
        p1 = DownloadProgress(status="pending", percent=0)
        p2 = DownloadProgress(status="pending", percent=0)
        assert p1 == p2
        # Different events, still equal.
        assert p1.cancel_event is not p2.cancel_event
