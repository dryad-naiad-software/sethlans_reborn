# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The manager's build queue for sequence videos and sound files (Unreal
sequence rendering D16, FR-SV-7).

One daemon thread in the serving process takes keys from a
``queue.Queue`` and runs one task per key
(``video_sequence.run_task``).  A key is ``(submission id, shot index or
None for the whole submission, item)``, item one of :data:`ITEMS`.  Keys
are queued after commit (:func:`enqueue_on_commit`) and a key already
waiting is not queued twice.  Nothing in one iteration can end the thread;
should it die anyway, the next :func:`enqueue` starts a new one; the task's compare-and-set claim (PENDING ->
ASSEMBLING with the boot id) decides who builds, so a duplicate is
harmless.

The thread is started only by the server start hook
(``video_startup.on_server_start``), never by ``apps.ready()``.  In any
other process (management commands, tests, the reloader's parent) the
queue does not accept keys: PENDING items wait for the next server start.

Test hooks: :func:`start_manual_for_tests` accepts keys without a thread,
:func:`run_pending_for_tests` runs them in the caller's thread, and
:func:`stop_for_tests` stops and joins the thread and forgets every key.
"""

import logging
import queue
import threading

from django.db import close_old_connections, connections, transaction

logger = logging.getLogger(__name__)

ITEMS = ('video', 'sound', 'sound_video')
THREAD_NAME = 'sethlans-build-queue'
_STOP = object()
_lock = threading.Lock()
_tasks = queue.Queue()
_queued = set()
_state = {'boot': '', 'thread': None, 'accepting': False}


def boot_id():
    """The serving process's boot id, or ``''`` when the queue is not running."""
    return _state['boot']


def is_accepting():
    return _state['accepting']


def start(boot):
    """Start the thread under ``boot`` (once per process); returns False when already started."""
    with _lock:
        if _state['accepting']:
            return False
        thread = threading.Thread(target=_loop, name=THREAD_NAME, daemon=True)
        _state.update(boot=boot, thread=thread, accepting=True)
    thread.start()
    logger.info("Build queue started (boot %s).", boot)
    return True


def enqueue(key):
    """Queue ``key`` unless the queue is not running or the key already waits; returns whether it was queued."""
    with _lock:
        if not _state['accepting'] or key in _queued:
            return False
        _revive()
        _queued.add(key)
    _tasks.put(key)
    return True


def _revive():
    """Under ``_lock``: start a new thread when the serving thread died (it never should; logged loudly)."""
    thread = _state['thread']
    if thread is None or thread.is_alive():
        return
    logger.error("The build queue's thread had stopped; starting a new one.")
    thread = threading.Thread(target=_loop, name=THREAD_NAME, daemon=True)
    _state['thread'] = thread
    thread.start()


def enqueue_on_commit(keys):
    """Queue ``keys`` once the current transaction commits (at once under autocommit)."""
    keys = list(keys)
    if keys:
        transaction.on_commit(lambda: [enqueue(key) for key in keys], robust=True)


def _take(block=True):
    key = _tasks.get(block=block)
    if key is not _STOP:
        with _lock:
            _queued.discard(key)
    return key


def _run(key):
    try:
        from .video_sequence import run_task
        run_task(key, _state['boot'])
    except Exception:  # noqa: BLE001 -- one failed task must not stop the queue
        logger.exception("Build task %s failed.", key)


def _close_old():
    try:
        close_old_connections()
    except Exception:  # noqa: BLE001 -- a bad connection must not end the thread
        logger.exception("Build queue: closing old database connections failed.")


def _loop():
    try:
        while True:
            key = _take()
            if key is _STOP:
                return
            _close_old()
            _run(key)
            _close_old()
    finally:
        try:
            connections.close_all()
        except Exception:  # noqa: BLE001
            logger.exception("Build queue: closing database connections failed.")


def start_manual_for_tests(boot):
    """Accept keys under ``boot`` without a thread; :func:`run_pending_for_tests` runs them."""
    with _lock:
        _state.update(boot=boot, thread=None, accepting=True)


def run_pending_for_tests():
    """Run every waiting key in the caller's thread; returns how many ran."""
    ran = 0
    while True:
        try:
            key = _take(block=False)
        except queue.Empty:
            return ran
        if key is not _STOP:
            _run(key)
            ran += 1


def stop_for_tests(timeout=10.0):
    """Stop accepting, stop and join the thread, forget every waiting key."""
    with _lock:
        thread = _state['thread']
        _state.update(boot='', thread=None, accepting=False)
    if thread is not None:
        _tasks.put(_STOP)
        thread.join(timeout)
    while True:
        try:
            _tasks.get(block=False)
        except queue.Empty:
            break
    with _lock:
        _queued.clear()
