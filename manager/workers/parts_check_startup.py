# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Start of the boot-time parts check (issue #294).

The parts check (:mod:`workers.services.parts_check`) finds FFmpeg and,
when it is not set in ``manager.ini``, not on ``PATH`` and not already
bundled, downloads it.  It runs only in a process that serves requests,
the same processes that start the Blender series fetch (issue #292,
:mod:`workers.release_cache_startup`):

* the Waitress serving process — ``sethlans_manager.waitress_launcher.launch``
  calls :func:`start_for_server` before the build queue's start hook
  (source and frozen builds, the Docker images and the ``--dev`` child);
* Django's ``runserver`` in development — ``WorkersConfig.ready()`` calls
  :func:`start_if_runserver`, which starts the check only in the
  process that serves (the autoreloader's child, or ``--noreload``).

Management commands (``migrate``, ``spectacular``, ``check``,
``collectstatic``, ``apply_pending_setup``, the frozen ``--manage``
path), the autoreloader's parent and the test runner never start it.
Nothing in those processes reads the FFmpeg status: its readers are the
FFmpeg status endpoint, the video settings validation and the video
builds, which all run in the serving process.

The server does not wait for the check (a download must not hold the
listeners back past the launcher's start deadline).  While it runs, the
build queue leaves its items PENDING instead of failing them for want of
FFmpeg (``video_sequence.run_task``), and once it ends,
:func:`_requeue_when_checked` queues every PENDING build again, so each
one runs with FFmpeg's real status.
"""

import logging
import threading

from django.db import connections

from .release_cache_startup import is_runserver_serving_process
from .services import parts_check

logger = logging.getLogger(__name__)

REQUEUE_THREAD_NAME = 'parts-check-requeue'


def start_parts_check() -> None:
    """Start the check thread; a second call in the same process is a no-op."""
    parts_check.run_parts_check()


def start_for_server() -> None:
    """Waitress serving-process hook: start the check, re-queue builds when it ends.

    Returns at once.  Call it before ``video_startup.on_server_start`` so
    the usual quick check (override, ``PATH``, bundled copy) has the
    best chance to finish before the first build runs.
    """
    start_parts_check()
    threading.Thread(
        target=_requeue_when_checked, name=REQUEUE_THREAD_NAME, daemon=True,
    ).start()


def _requeue_when_checked() -> None:
    """Wait for the check, then queue the PENDING builds again (thread body).

    When the build queue is not accepting yet, nothing could have been
    left waiting: ``on_server_start`` queues the PENDING builds itself
    after it starts the queue, and the check has finished by then.
    Queuing twice is harmless (a key already waiting is not queued
    again, and the claim decides who builds).
    """
    from . import video_queue
    from .video_startup import queue_pending

    parts_check.wait_for_checks(None)
    if not video_queue.is_accepting():
        return
    try:
        queued = queue_pending()
        logger.info("Parts check finished; %s versions' builds queued again.", queued)
    except Exception:  # noqa: BLE001 -- a daemon thread must not die loudly
        logger.exception("Queuing the builds again after the parts check failed.")
    finally:
        connections.close_all()


def start_if_runserver() -> bool:
    """``AppConfig.ready()`` hook: start the check in runserver's serving process only."""
    if not is_runserver_serving_process():
        return False
    start_parts_check()
    return True
