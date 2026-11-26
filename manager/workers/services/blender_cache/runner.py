# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Blender cache runner: one daemon thread in the serving process (BS-MGR-5, BS-MGR-6, BS-MGR-16).

:func:`start_for_server` is called once by
``sethlans_manager.waitress_launcher.launch`` (never from ``apps.ready()``,
which also runs for management commands). Its steps, each logged and
swallowed so the server still starts:

1. FETCHING rows a stopped process left go back to QUEUED;
2. READY rows whose file is missing go back to QUEUED (BS-MGR-9);
3. ``reconcile()`` (rows only; it runs even with fetching off);
4. the runner thread starts, unless ``release_cache_startup.fetch_disabled()``
   (pytest, ``SETHLANS_DISABLE_RELEASE_FETCH=1``) or the root overlaps
   ``MEDIA_ROOT``.

The thread is the only code that touches files under the cache root. Each
round it reconciles, re-queues FETCHING rows (left when both result writes of
a fetch failed; this thread is the only claimer and no fetch runs between
rounds), fetches every due row one at a time, sweeps, and then
waits on ``_wake`` for at most 10 minutes; :func:`reconcile_and_wake` (the
on-commit hook of supported-version changes and of a registration with a new
platform) and the metadata view set it. The timeout makes the reconcile run
every 10 minutes, which covers ``check_blender_updates`` (a queryset
``update()`` in another process, no signal). Database connections are closed
around each fetch and on exit, as in ``video_queue._loop``.
"""

import logging
import threading
from typing import Optional

from django.db import close_old_connections, connections

from . import fetcher, paths, sweep, wanted

logger = logging.getLogger(__name__)

THREAD_NAME = 'blender-cache-runner'
WAIT_SECONDS = 10 * 60

_wake = threading.Event()
_stop = threading.Event()
_lock = threading.Lock()
_state = {'thread': None, 'started': False}


def wake() -> None:
    """Ask the runner for a round now (no-op when no runner runs)."""
    _wake.set()


def is_alive() -> bool:
    """True while this process's runner thread runs (BS-MGR-10: only then may a build be ``queued``)."""
    thread: Optional[threading.Thread] = _state['thread']
    return thread is not None and thread.is_alive()


def reconcile_and_wake() -> None:
    """Reconcile the rows, then wake the runner (BS-MGR-5); for ``transaction.on_commit(..., robust=True)``."""
    wanted.reconcile()
    wake()


def _step(name, function, *args):
    try:
        return function(*args)
    except Exception:  # noqa: BLE001 -- the server must start; the runner must keep running
        logger.exception("Blender cache: %s failed.", name)
        return None


def _close_old() -> None:
    _step("closing old database connections", close_old_connections)


def _fetch_due(root, stop) -> None:
    while not stop.is_set():
        row = _step("claiming a build", fetcher.claim_next)
        if row is None:
            return
        _close_old()
        _step(f"fetching {row.file_name}", fetcher.fetch, row, root, stop)
        _close_old()


def _round(stop) -> None:
    root = paths.cache_root()
    _close_old()
    _step("reconciling the wanted builds", wanted.reconcile)
    # A row left FETCHING (both result writes of a fetch failed) is claimable
    # again. Safe: this thread is the only claimer, and no fetch runs here.
    _step("re-queuing interrupted fetches", wanted.requeue_interrupted)
    _fetch_due(root, stop)
    if not stop.is_set():
        _step("sweeping the cache", sweep.sweep, root)
    _close_old()


def _loop(stop) -> None:
    try:
        while not stop.is_set():
            _wake.clear()  # a wake during the round below starts the next round at once
            _round(stop)
            if stop.is_set():
                break
            _wake.wait(WAIT_SECONDS)
    finally:
        try:
            connections.close_all()
        except Exception:  # noqa: BLE001
            logger.exception("Blender cache: closing database connections failed.")


def _start_thread() -> bool:
    from ...release_cache_startup import fetch_disabled
    root = paths.cache_root()
    if fetch_disabled():
        logger.info("Blender cache runner not started: Blender fetching is turned off.")
        return False
    paths.check_root(root)
    root.mkdir(parents=True, exist_ok=True)
    with _lock:
        if _state['thread'] is not None:
            return False
        _stop.clear()
        thread = threading.Thread(target=_loop, args=(_stop,), name=THREAD_NAME, daemon=True)
        _state['thread'] = thread
        thread.start()
    logger.info("blender_cache: runner started root=%s", root)
    return True


def start_for_server() -> bool:
    """Start steps of the serving process; True when the runner thread started. Once per process."""
    with _lock:
        if _state['started']:
            return False
        _state['started'] = True
    _step("re-queuing interrupted fetches", wanted.requeue_interrupted)
    _step("re-queuing builds missing on disk", wanted.requeue_missing_files)
    _step("reconciling the wanted builds", wanted.reconcile)
    return bool(_step("starting the runner", _start_thread))


def stop(join_timeout: float = 5.0) -> None:
    """Stop the runner (a download in progress stops between chunks) and join it."""
    _stop.set()
    _wake.set()
    thread = _state['thread']
    if thread is not None:
        thread.join(timeout=join_timeout)


def reset_for_tests() -> None:
    """Forget a stopped runner so a test can start another one."""
    stop()
    with _lock:
        _state.update(thread=None, started=False)
    _stop.clear()
    _wake.clear()
