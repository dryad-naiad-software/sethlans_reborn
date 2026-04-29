# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""FFmpeg download orchestration (FR-M2-7 / FR-M2-7a / FR-M2-7b).

Three endpoints sharing a single per-process task registry guarded by
the "ffmpeg-task lock":

* ``POST /api/wizard/ffmpeg/start/`` — kicks off (or returns the
  in-progress) download. Single-task invariant.
* ``GET /api/wizard/ffmpeg/progress/<task_id>/`` — snapshot of status
  / percent / category / error.
* ``POST /api/wizard/ffmpeg/cancel/`` — sets the task's
  ``threading.Event`` so the download worker exits between chunks.

The download pipeline lives in
:mod:`wizard.sethlans_wizard.ffmpeg_worker` (extracted to keep this
module under the 300-line ceiling); this module owns the WSGI surface
and the per-process task registry.
"""

from __future__ import annotations

import logging
import secrets
import threading
from pathlib import Path
from typing import Callable, Iterable, Optional

from wizard.sethlans_wizard import ffmpeg_download as ffdl, progress, wizard_state
from wizard.sethlans_wizard.checkpoints import FFMPEG_INSTALLED
from wizard.sethlans_wizard.ffmpeg_worker import download_worker
from wizard.sethlans_wizard.handlers import _wsgi
from wizard.sethlans_wizard.handlers.auth import session_header_valid

logger = logging.getLogger(__name__)

# FR-M2-7a — single per-process task lock.
_task_lock: threading.Lock = threading.Lock()

# Single-task invariant: at most one of these populated at a time.
_active_task: Optional[dict] = None


def _new_task_id() -> str:
    return secrets.token_urlsafe(12)


def _snapshot_locked() -> Optional[dict]:
    if _active_task is None:
        return None
    return {
        "task_id": _active_task["task_id"],
        "status": _active_task["status"],
        "percent": _active_task["percent"],
        "category": _active_task.get("category"),
        "error": _active_task.get("error"),
    }


def _set_status(
    task_id: str,
    status: Optional[str] = None,
    percent: Optional[int] = None,
    category: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Mutate the active task. Acquires the lock for the update."""
    with _task_lock:
        if _active_task is None or _active_task["task_id"] != task_id:
            return
        if status is not None:
            _active_task["status"] = status
        if percent is not None:
            _active_task["percent"] = percent
        if category is not None:
            _active_task["category"] = category
        if error is not None:
            _active_task["error"] = error


# ---- WSGI handler factories ----

def make_start_handler(data_dir: Path) -> Callable:
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)

    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        return _handle_start(environ, start_response, data_dir)

    return handler


def make_progress_handler() -> Callable:
    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        return _handle_progress(environ, start_response)

    return handler


def make_cancel_handler() -> Callable:
    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        return _handle_cancel(environ, start_response)

    return handler


# ---- Dispatchers ----

def _handle_start(
    environ: dict,
    start_response: Callable,
    data_dir: Path,
) -> Iterable[bytes]:
    global _active_task
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "POST":
        return _wsgi.send_json(
            start_response,
            {"error": "Method Not Allowed"},
            status=405,
            extra_headers=[("Allow", "POST")],
        )
    if not session_header_valid(environ):
        return _wsgi.send_json(
            start_response,
            {"error": "missing or invalid X-Wizard-Session header"},
            status=401,
        )

    # Concurrency-reviewer C1 — single-task invariant + the already-
    # installed short-circuit MUST both be evaluated under ``_task_lock``.
    # Checking ``already_installed`` outside the lock allows a second
    # start mid-extraction to clobber the in-flight task. Lock-ordering:
    # ``_task_lock`` is acquired alone here; ``wizard_state.set_ffmpeg``
    # and ``progress.append_checkpoint`` (each with their own lock) run
    # OUTSIDE ``_task_lock`` to avoid nesting.
    short_circuit_task_id: Optional[str] = None
    spawn_args: Optional[tuple[str, threading.Event]] = None
    binary_for_state: Optional[Path] = None
    with _task_lock:
        if _active_task is not None and _active_task["status"] not in (
            "complete", "failed",
        ):
            # In-flight task wins — return its id regardless of disk
            # state. Single-task invariant (FR-M2-7a).
            return _wsgi.send_json(
                start_response,
                {"status": "in_progress",
                 "task_id": _active_task["task_id"]},
                status=200,
            )
        # No in-flight task. Decide between short-circuit and spawn.
        if ffdl.already_installed(data_dir):
            binary = ffdl.get_ffmpeg_binary(data_dir)
            if binary is not None:
                _active_task = {
                    "task_id": _new_task_id(),
                    "status": "complete",
                    "percent": 100,
                    "category": None,
                    "error": None,
                    "cancel_event": threading.Event(),
                }
                short_circuit_task_id = _active_task["task_id"]
                binary_for_state = binary
        if short_circuit_task_id is None:
            cancel_event = threading.Event()
            new_id = _new_task_id()
            _active_task = {
                "task_id": new_id,
                "status": "downloading",
                "percent": 0,
                "category": None,
                "error": None,
                "cancel_event": cancel_event,
            }
            spawn_args = (new_id, cancel_event)

    # Side effects that touch other modules' locks happen OUTSIDE
    # ``_task_lock`` to keep the lock-ordering convention simple.
    if short_circuit_task_id is not None and binary_for_state is not None:
        wizard_state.set_ffmpeg(ffdl.FFMPEG_VERSION, str(binary_for_state))
        progress.append_checkpoint(data_dir, FFMPEG_INSTALLED)
        return _wsgi.send_json(
            start_response,
            {"status": "in_progress", "task_id": short_circuit_task_id},
            status=200,
        )

    assert spawn_args is not None  # nosec: invariant of branch above
    new_id, cancel_event = spawn_args
    thread = threading.Thread(
        target=download_worker,
        args=(new_id, data_dir, cancel_event, _set_status),
        daemon=True,
        name=f"wizard-ffmpeg-{new_id}",
    )
    thread.start()
    return _wsgi.send_json(
        start_response,
        {"status": "started", "task_id": new_id},
        status=200,
    )


def _handle_progress(
    environ: dict,
    start_response: Callable,
) -> Iterable[bytes]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "GET":
        return _wsgi.send_json(
            start_response,
            {"error": "Method Not Allowed"},
            status=405,
            extra_headers=[("Allow", "GET")],
        )
    if not session_header_valid(environ):
        return _wsgi.send_json(
            start_response,
            {"error": "missing or invalid X-Wizard-Session header"},
            status=401,
        )
    path = environ.get("PATH_INFO", "") or ""
    parts = path.rstrip("/").split("/")
    task_id = parts[-1] if parts else ""
    if not task_id:
        return _wsgi.send_json(
            start_response,
            {"error": "missing task_id"},
            status=400,
        )
    with _task_lock:
        snapshot = _snapshot_locked()
    if snapshot is None or snapshot["task_id"] != task_id:
        return _wsgi.send_json(
            start_response,
            {"error": "unknown_task"},
            status=404,
        )
    return _wsgi.send_json(start_response, snapshot, status=200)


def _handle_cancel(
    environ: dict,
    start_response: Callable,
) -> Iterable[bytes]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "POST":
        return _wsgi.send_json(
            start_response,
            {"error": "Method Not Allowed"},
            status=405,
            extra_headers=[("Allow", "POST")],
        )
    if not session_header_valid(environ):
        return _wsgi.send_json(
            start_response,
            {"error": "missing or invalid X-Wizard-Session header"},
            status=401,
        )
    with _task_lock:
        if _active_task is None or _active_task["status"] in (
            "complete", "failed",
        ):
            return _wsgi.send_json(
                start_response,
                {"status": "not_found"},
                status=200,
            )
        _active_task["cancel_event"].set()
        _active_task["status"] = "failed"
        _active_task["category"] = "download_failed"
        _active_task["error"] = "cancelled by user"
    return _wsgi.send_json(
        start_response,
        {"status": "cancelled"},
        status=200,
    )


def reset_for_tests() -> None:
    """Wipe the active task. Test-only helper."""
    global _active_task
    with _task_lock:
        _active_task = None


__all__ = [
    "make_start_handler",
    "make_progress_handler",
    "make_cancel_handler",
    "reset_for_tests",
]
