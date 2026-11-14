# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cancel watcher of an Unreal job (Unreal foundation FR-UE-3; Conc M6, L5).

``CancelCause`` is a ``threading.Event`` plus a cause recorded
**first-writer-wins** under a leaf lock: ``'canceled'`` (the job's status
is ``CANCELED``), ``'reassigned'`` (status not ``RENDERING`` or another
assignee) or ``'shutdown'`` (``ctx.shutdown_event``). It has ``is_set()``,
so it doubles as the ``Abort.cancel`` of the archive pipeline.

``CancelWatcher`` is a **daemon** thread ``unreal-cancel-<job_id>`` that
loops on ``stop_event.wait(CANCEL_POLL_SECONDS)`` and calls
``get_assignment(job_id)``; a ``None`` result (network error) is ignored
and every exception is logged at WARNING, so the thread never dies with
an uncaught exception. ``stop()`` sets the stop event and joins with a
10 s timeout; a watcher blocked in a network call cannot hold up exit.
"""
import logging
import threading
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

CAUSES = ('canceled', 'shutdown', 'reassigned')
JOIN_TIMEOUT_SECONDS = 10.0


class CancelCause:

    def __init__(self):
        self._lock = threading.Lock()  # leaf lock
        self._event = threading.Event()
        self._cause: Optional[str] = None

    def set(self, cause: str) -> bool:
        """Record ``cause`` unless one is already recorded; True if this call won."""
        if cause not in CAUSES:
            raise ValueError(f"unknown cancel cause {cause!r}")
        with self._lock:
            if self._cause is not None:
                return False
            self._cause = cause
        self._event.set()
        return True

    @property
    def cause(self) -> Optional[str]:
        with self._lock:
            return self._cause

    def is_set(self) -> bool:
        return self._event.is_set()


def classify(result: Optional[Tuple[str, Optional[int]]], worker_id) -> Optional[str]:
    """The cause a ``get_job_assignment`` result implies, or None."""
    if result is None:
        return None
    status, assignee = result
    if status == 'CANCELED':
        return 'canceled'
    if status != 'RENDERING' or worker_id is None or assignee != worker_id:
        return 'reassigned'
    return None


class CancelWatcher:

    def __init__(self, job_id: int, cause: CancelCause, *, get_assignment: Callable, worker_id,
                 shutdown_event: Optional[threading.Event], poll_seconds: float):
        self.job_id = job_id
        self.cause = cause
        self._get = get_assignment
        self._worker_id = worker_id
        self._shutdown = shutdown_event
        self._poll = poll_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _check_once(self) -> None:
        if self._shutdown is not None and self._shutdown.is_set():
            self.cause.set('shutdown')
            return
        found = classify(self._get(self.job_id), self._worker_id)
        if found is not None and self.cause.set(found):
            logger.warning("Unreal job %s: %s.", self.job_id, found)

    def _run(self) -> None:
        while not self._stop.wait(self._poll):
            try:
                self._check_once()
            except Exception as exc:  # noqa: BLE001 - the watcher must never die (pytest.ini)
                logger.warning("Cancel watcher for job %s: %s", self.job_id, exc)
            if self.cause.is_set():
                return

    def start(self) -> None:
        """Assigned only once started: ``stop()`` never joins a thread whose ``start()`` failed (review L3)."""
        thread = threading.Thread(target=self._run, name=f'unreal-cancel-{self.job_id}', daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.ident is not None and thread is not threading.current_thread():
            thread.join(JOIN_TIMEOUT_SECONDS)
