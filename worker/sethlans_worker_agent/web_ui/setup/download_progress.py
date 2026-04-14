# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Download progress tracking for setup wizard background tasks.

Uses frozen dataclasses for thread-safe progress reporting.  Each
download task is tracked by a ``task_id`` in a module-level dict.
Progress updates replace the entire dict entry atomically rather than
mutating individual fields.

The ``cancel_event`` on each ``DownloadProgress`` is a mutable
``threading.Event`` that the download thread checks between chunks.
It is excluded from frozen-dataclass comparison.

Ported from ``manager/workers/services/download_progress.py``.
"""

import secrets
import threading
from dataclasses import dataclass, field
from typing import Optional

# Module-level task registry.  Keyed by task_id.
_download_tasks: dict[str, "DownloadProgress"] = {}
_tasks_lock = threading.Lock()


@dataclass(frozen=True)
class DownloadProgress:
    """Immutable snapshot of a download task's state."""

    status: str = "pending"
    # pending | downloading | extracting | verifying | complete | failed
    percent: int = 0
    error: Optional[str] = None
    cancel_event: threading.Event = field(
        default_factory=threading.Event,
        compare=False,
        repr=False,
    )


def create_task() -> tuple[str, DownloadProgress]:
    """Create a new tracked download task.

    Returns ``(task_id, progress)`` with the task already registered.
    """
    task_id = secrets.token_urlsafe(16)
    progress = DownloadProgress()
    with _tasks_lock:
        _download_tasks[task_id] = progress
    return task_id, progress


def create_tagged_task(tag: str) -> tuple[str, DownloadProgress]:
    """Like ``create_task`` but prefixes the id with *tag*."""
    task_id = f"{tag}{secrets.token_urlsafe(16)}"
    progress = DownloadProgress()
    with _tasks_lock:
        _download_tasks[task_id] = progress
    return task_id, progress


def get_task(task_id: str) -> Optional[DownloadProgress]:
    """Return the current progress snapshot, or ``None``."""
    with _tasks_lock:
        return _download_tasks.get(task_id)


def update_task(task_id: str, **kwargs) -> None:
    """Replace the progress entry, preserving the cancel_event."""
    with _tasks_lock:
        current = _download_tasks.get(task_id)
        if current is None:
            return
        # Carry forward cancel_event unless explicitly overridden.
        if "cancel_event" not in kwargs:
            kwargs["cancel_event"] = current.cancel_event
        _download_tasks[task_id] = DownloadProgress(**kwargs)


def remove_task(task_id: str) -> None:
    """Remove a completed/failed task from the registry."""
    with _tasks_lock:
        _download_tasks.pop(task_id, None)


def find_active_task(
    prefix: str,
) -> Optional[tuple[str, DownloadProgress]]:
    """Find an in-progress task whose id starts with *prefix*.

    Used to guard against duplicate download starts.  The ``prefix``
    is a category tag set by the caller (e.g. ``"blender_"``).
    """
    with _tasks_lock:
        for tid, prog in _download_tasks.items():
            if tid.startswith(prefix) and prog.status in (
                "pending", "downloading", "extracting", "verifying",
            ):
                return tid, prog
    return None
