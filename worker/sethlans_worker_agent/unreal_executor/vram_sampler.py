# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU memory sampler thread (Unreal foundation FR-WD-10).

A daemon thread ``unreal-vram-sampler`` refreshes one cached
``GpuMemoryReading`` every ``VRAM_SAMPLE_SECONDS`` and catches every
exception. Started only when Unreal is enabled (NF-6), it is the only
caller of the reader (a reader call can block without limit in a driver).

* ``latest()`` returns the cached reading without blocking (heartbeat).
* ``fresh(max_age_s, deadline, requested_at=None)`` returns the cached
  reading when it is at most ``max_age_s`` old; otherwise it asks the
  thread for **one** sample and waits until ``deadline`` (absolute
  ``time.monotonic()``). The first sample that *started* at or after the
  request (``requested_at``, default now; startup passes the thread's
  start) is final, even when it failed: at most one extra sample (D1).
* ``never_succeeded()``: the thread was started or a sample completed,
  and no sample has produced a reading (F6: Unreal reported disabled).

A failed sample caches None: the gate skips, the heartbeat omits the key.
A sample over ``SLOW_SAMPLE_SECONDS`` logs one WARNING per process.

Locking: one ``threading.Condition`` (a leaf lock) guards the reading,
the sample bookkeeping and the wake-up event; the sample runs outside it.
"""
import logging
import threading
import time
from typing import Callable, Optional

from sethlans_worker_agent.unreal_executor.vram import GpuMemoryReading

logger = logging.getLogger(__name__)

SLOW_SAMPLE_SECONDS = 10.0
_slow_lock = threading.Lock()  # leaf lock
_slow_logged = False


def _note_duration(seconds: float) -> None:
    global _slow_logged
    with _slow_lock:
        if seconds <= SLOW_SAMPLE_SECONDS or _slow_logged:
            return
        _slow_logged = True
    logger.warning("GPU memory sample took %.0f s", seconds)


class VramSampler:

    def __init__(self, sample_fn: Callable[[], Optional[GpuMemoryReading]],
                 interval_seconds: float, clock: Callable[[], float] = time.monotonic):
        self._sample_fn = sample_fn
        self._interval = interval_seconds
        self._clock = clock
        self._cond = threading.Condition(threading.Lock())  # leaf lock
        self._reading: Optional[GpuMemoryReading] = None
        self._last_started: Optional[float] = None  # start time of the last completed sample
        self._running_since: Optional[float] = None  # start time of the sample in progress
        self._samples = 0
        self._ever_succeeded = False
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def latest(self) -> Optional[GpuMemoryReading]:
        with self._cond:
            return self._reading

    def never_succeeded(self) -> bool:
        with self._cond:
            return (self._samples > 0 or self._thread is not None) and not self._ever_succeeded

    def fresh(self, max_age_s: float, deadline: float,
              requested_at: Optional[float] = None) -> Optional[GpuMemoryReading]:
        requested_at = self._clock() if requested_at is None else requested_at
        with self._cond:
            reading = self._reading
            if reading is not None and requested_at - reading.taken_at <= max_age_s:
                return reading
            done = self._last_started is not None and self._last_started >= requested_at
            running = self._running_since is not None and self._running_since >= requested_at
            if done:
                return self._reading  # a sample that counts already finished (even a failed one)
            if not running:
                self._wake.set()  # no sample that counts is running: ask for one
            while self._last_started is None or self._last_started < requested_at:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            return self._reading

    def sample_once(self) -> Optional[GpuMemoryReading]:
        with self._cond:
            started = self._running_since = self._clock()
            self._wake.clear()  # this sample answers every request made before it started
        try:
            reading = self._sample_fn()
        except Exception:  # noqa: BLE001 - the sampler must survive (NF-5)
            logger.exception("GPU memory sample failed.")
            reading = None
        _note_duration(self._clock() - started)
        with self._cond:
            self._reading = reading
            self._last_started, self._running_since = started, None
            self._samples += 1
            self._ever_succeeded = self._ever_succeeded or reading is not None
            self._cond.notify_all()
        return reading

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample_once()
            if self._stop.is_set():
                break
            self._wake.wait(self._interval)

    def start(self) -> None:
        """Start the thread; it takes its first sample at once."""
        with self._cond:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name='unreal-vram-sampler', daemon=True)
        self._thread.start()

    def stop(self, timeout: Optional[float] = None) -> bool:
        """Stop the thread; True when it is no longer running (or never started)."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True
