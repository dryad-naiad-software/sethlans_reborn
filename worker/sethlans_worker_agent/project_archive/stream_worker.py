# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Abandonable daemon workers for share I/O (Unreal foundation FR-PA-4,
FR-PA-12, FR-UE-2; review fix Conc C2).

SMB reads cannot be interrupted, so every share read (the archive copy and
the overlay walk and copy) runs on a **daemon worker thread** while the
job thread waits on it, checking every ``poll_seconds`` for cancel,
shutdown (directly), the deadline (``Abort.check``) and a stall: no
``job.progress()`` call for ``stall_seconds`` raises ``ShareStallError``.

On any of these the job thread marks the job **abandoned** and returns at
once; it never waits for a blocked read. The worker checks
``job.is_abandoned()`` after every read and stops. Exactly one of the two
threads runs ``cleanup`` (removing the worker's partial output): the
worker when it finishes after an abandon or with an error, or the job
thread when it abandons a worker that already finished. That decision is
made under the job's leaf lock. The worker catches every exception
(NF-5); ``target`` raises the pipeline's own error types, which are
re-raised on the job thread.
"""
import logging
import threading
import time
import uuid
from typing import Callable, Optional

from sethlans_worker_agent.project_archive.abort import Abort
from sethlans_worker_agent.project_archive.errors import ShareStallError

logger = logging.getLogger(__name__)

PROGRESS_LOG_SECONDS = 30.0


class StreamJob:
    """Shared state between the job thread and one worker."""

    def __init__(self, cleanup: Callable[[], None], clock: Callable[[], float] = time.monotonic):
        self._cleanup = cleanup
        self._clock = clock
        self.lock = threading.Lock()  # leaf lock
        self.done = threading.Event()
        self.abandoned = False
        self.finished = False
        self.error: Optional[BaseException] = None
        self.result = None
        self.count = 0
        self.last_progress = clock()

    def progress(self, count: Optional[int] = None) -> None:
        with self.lock:
            if count is not None:
                self.count = count
            self.last_progress = self._clock()

    def snapshot(self):
        with self.lock:
            return self.count, self.last_progress

    def is_abandoned(self) -> bool:
        with self.lock:
            return self.abandoned

    def _run_cleanup(self) -> None:
        try:
            self._cleanup()
        except Exception:  # noqa: BLE001 - cleanup must not raise from either thread
            logger.exception("Cleaning up an abandoned share transfer failed.")

    def finish(self) -> None:
        """Worker: done. Cleans up when abandoned or failed."""
        with self.lock:
            self.finished = True
            clean = self.abandoned or self.error is not None
        if clean:
            self._run_cleanup()
        self.done.set()

    def abandon(self) -> None:
        """Job thread: stop waiting. Cleans up if the worker already finished."""
        with self.lock:
            self.abandoned = True
            clean = self.finished
        if clean:
            self._run_cleanup()


def _wait(job: StreamJob, abort: Abort, stall_seconds: float, poll_seconds: float, clock, what: str) -> None:
    next_log = clock() + PROGRESS_LOG_SECONDS
    while not job.done.wait(poll_seconds):
        abort.check()
        count, last = job.snapshot()
        now = clock()
        if now - last > stall_seconds:
            raise ShareStallError(f"No data from the Sethlans share for {stall_seconds:g} s while {what}.")
        if now >= next_log:
            logger.info("Unreal share transfer (%s): %d bytes so far.", what, count)
            next_log = now + PROGRESS_LOG_SECONDS


def run(target: Callable[[StreamJob], object], *, what: str, abort: Abort, stall_seconds: float,
        cleanup: Callable[[], None], poll_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic):
    """Run ``target(job)`` on a daemon worker and wait for it; see the module docstring."""
    job = StreamJob(cleanup, clock)

    def body():
        try:
            job.result = target(job)
        except Exception as exc:  # noqa: BLE001 - re-raised on the job thread
            job.error = exc
        finally:
            job.finish()

    thread = threading.Thread(target=body, name=f'unreal-share-{uuid.uuid4().hex[:8]}', daemon=True)
    try:
        thread.start()
    except BaseException as exc:
        job.error = exc
        job.finish()
        raise
    try:
        _wait(job, abort, stall_seconds, poll_seconds, clock, what)
    except BaseException:
        job.abandon()
        raise
    if job.error is not None:
        raise job.error
    return job.result
