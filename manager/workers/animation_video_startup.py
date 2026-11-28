# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Server-start recovery of interrupted animation video builds (issue #295).

An animation's video is built in a thread of the serving process
(``video_assembler.assemble_animation_video``, started by
``parent_dispatch._trigger_video_assembly`` or the retry-video action).
When that process stops mid-build, the row stays ``ASSEMBLING`` with no
thread behind it.  :func:`reset_interrupted_assemblies` marks those rows
``ERROR`` so the user can retry them.

That is only right in a serving process that is starting, before it can
start a build of its own:

* the Waitress serving process — ``sethlans_manager.waitress_launcher.launch``
  calls :func:`reset_for_server` first among its start hooks, before the
  listeners accept requests (source and frozen builds, the Docker images
  and the ``--dev`` child);
* Django's ``runserver`` in development — ``WorkersConfig.ready()`` calls
  :func:`reset_if_runserver`, which resets only in the process that
  serves (the autoreloader's child, or ``--noreload``).

``AppConfig.ready()`` also runs for every management command
(``migrate``, ``check``, ``spectacular``, ``collectstatic``,
the frozen ``--manage`` path), for the
autoreloader's parent and for ``run_manager.py``'s own migrate /
collectstatic subprocesses.  Those run beside a live server, so they
must never reset: they would mark its running builds failed.  This is
the same gate as the Blender series fetch (#292) and the parts check
(#294).

Every build is started by a request (a job update, retry-video, a
requeue or a project cancel), so resetting before the listeners start
cannot touch a build of this process.  The sequence build queue
(``video_startup``) keeps its own reset of sequence items by boot id.
"""

import logging
import threading

from django.db import OperationalError, ProgrammingError

from .release_cache_startup import is_runserver_serving_process

logger = logging.getLogger(__name__)

INTERRUPTED_MESSAGE = 'Video assembly was interrupted by server restart.'

_lock = threading.Lock()
_done = False


def reset_interrupted_assemblies() -> int:
    """Mark every animation ``ASSEMBLING`` as ``ERROR``; returns how many.

    A database without the column yet (migrations not run) is skipped
    and counts as none.
    """
    from .models import Animation

    try:
        updated = Animation.objects.filter(video_status='ASSEMBLING').update(
            video_status='ERROR', video_error=INTERRUPTED_MESSAGE,
        )
    except (OperationalError, ProgrammingError) as exc:
        # Usually a database not migrated yet; "database is locked" and the
        # like land here too, so leave a trace.
        logger.warning("Interrupted animation video reset skipped: %s", exc)
        return 0
    if updated:
        logger.info(
            "Reset %d animation(s) from ASSEMBLING to ERROR "
            "(server restart recovery).", updated,
        )
    return updated


def reset_for_server() -> bool:
    """Serving-process hook: reset once per process; returns True if it ran.

    A second call in the same process is a no-op: by then this process
    may be building videos itself.
    """
    global _done
    with _lock:
        if _done:
            return False
        _done = True
    reset_interrupted_assemblies()
    return True


def reset_if_runserver() -> bool:
    """``AppConfig.ready()`` hook: reset in runserver's serving process only."""
    if not is_runserver_serving_process():
        return False
    return reset_for_server()
