# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU memory sampler thread (Unreal foundation FR-WD-10).

A daemon thread ``unreal-vram-sampler`` refreshes one cached
``GpuMemoryReading`` every ``VRAM_SAMPLE_SECONDS`` and catches every
exception. It is started only when Unreal is enabled (NF-6).

* ``latest()`` returns the cached reading without blocking (heartbeat).
* ``fresh(max_age_s, deadline)`` returns the cached reading when it is
  at most ``max_age_s`` old; otherwise it asks the thread for **one**
  immediate sample and waits for it until ``deadline`` (an absolute
  ``time.monotonic()`` value). The first sample that *started* at or
  after the request is final, even when it failed (None): a call causes
  at most one extra sample (D1), so a broken source cannot spin.
  ``try_reserve`` uses ``max_age_s=5`` and the end of the per-poll probe
  budget (Dom N-1: ``used_mb`` is read at reserve time).
* ``never_succeeded()`` is True once a sample has completed and none has
  ever produced a reading (F6: Unreal is then reported disabled).

A failed sample replaces the cached reading with None ("any failure
yields None"): the gate skips and the heartbeat omits the key.

Locking: one ``threading.Condition`` (a leaf lock) guards the reading and
the sample bookkeeping; the sample itself runs outside it.
"""
import logging
import threading
import time
from typing import Callable, Optional

from sethlans_worker_agent.unreal_executor.vram import GpuMemoryReading

logger = logging.getLogger(__name__)


class VramSampler:

    def __init__(self, sample_fn: Callable[[], Optional[GpuMemoryReading]],
                 interval_seconds: float, clock: Callable[[], float] = time.monotonic):
        self._sample_fn = sample_fn
        self._interval = interval_seconds
        self._clock = clock
        self._cond = threading.Condition(threading.Lock())  # leaf lock
        self._reading: Optional[GpuMemoryReading] = None
        self._last_started: Optional[float] = None  # start time of the last completed sample
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
            return self._samples > 0 and not self._ever_succeeded

    def fresh(self, max_age_s: float, deadline: float) -> Optional[GpuMemoryReading]:
        requested_at = self._clock()
        with self._cond:
            reading = self._reading
            if reading is not None and requested_at - reading.taken_at <= max_age_s:
                return reading
        self._wake.set()
        with self._cond:
            while self._last_started is None or self._last_started < requested_at:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            return self._reading

    def sample_once(self) -> Optional[GpuMemoryReading]:
        started = self._clock()
        try:
            reading = self._sample_fn()
        except Exception:  # noqa: BLE001 - the sampler must survive (NF-5)
            logger.exception("GPU memory sample failed.")
            reading = None
        with self._cond:
            self._reading = reading
            self._last_started = started
            self._samples += 1
            self._ever_succeeded = self._ever_succeeded or reading is not None
            self._cond.notify_all()
        return reading

    def _run(self, skip_first: bool) -> None:
        if skip_first:
            self._wake.wait(self._interval)
        while not self._stop.is_set():
            self._wake.clear()
            self.sample_once()
            self._wake.wait(self._interval)

    def start(self, initial_sample_done: bool = False) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, args=(initial_sample_done,),
                                            name='unreal-vram-sampler', daemon=True)
            self._thread.start()

    def stop(self, timeout: Optional[float] = None) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
