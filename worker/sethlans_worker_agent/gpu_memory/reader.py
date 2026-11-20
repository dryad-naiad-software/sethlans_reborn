# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The process-wide GPU memory reader: one interface, one backend per platform.

``get_reader().read(pids)`` selects a backend on its first call, then
calls ``backend.sample(pids())``. It never raises: an exception is logged
(first at ERROR with the traceback, then at DEBUG) and gives ``None``.

Selection: the candidates of ``[gpu_memory] source`` (``candidates``:
``auto`` is ``pdh`` then ``nvidia-smi`` on 64-bit Windows, ``nvidia-smi``
on 32-bit Windows and Linux, none on macOS; a pinned name only that
backend) are tried in order; one is chosen when ``open()``
is True **and** its first sample has a card. A rejected candidate is
closed (a failing ``close()`` is logged, never skips the next one). The
chosen backend is kept for the life of the process; a failed sample gives
``None`` and never reselects. With none chosen, ``source`` is ``'none'``
and selection is retried at least ``RESELECT_SECONDS`` later.

A reading with no card or breaking a rule of ``types.problem`` is
dropped (WARNING once). The last ``STATS_WINDOW`` sample durations are
kept; one INFO line gives n, p50, p95, max at most every ``STATS_SECONDS``.

Locking: ``_sample_lock`` (leaf) is held during selection and one
``backend.sample()``; ``close()`` takes it. A backend call can block
(``nvidia-smi`` 10 s; Windows API calls have no timeout), so only the
sampler thread calls ``read()``; other consumers wait on the sampler with
a deadline. The singleton is created under a module-level leaf lock.
"""
import logging
import math
import sys
import threading
import time
from collections import deque
from typing import Callable, Dict, Optional, Protocol, Tuple

from sethlans_worker_agent.gpu_memory.types import GpuMemoryReading, problem

logger = logging.getLogger(__name__)

RESELECT_SECONDS = 600.0
STATS_SECONDS = 600.0
STATS_WINDOW = 600
SOURCE_NONE = 'none'


class Backend(Protocol):
    """A GPU memory source. Optional ``before_sample()`` (backends may omit it): called untimed, failures
    swallowed, before each sample once chosen; ``pdh`` uses it for its one cross-check after the first good sample."""
    name: str

    def open(self) -> bool: ...

    def sample(self, pids: frozenset) -> Optional[GpuMemoryReading]: ...

    def close(self) -> None: ...


def _pdh_backend() -> Backend:
    from sethlans_worker_agent.gpu_memory import win_backend  # imported only when pdh is tried
    return win_backend.PdhBackend()


def _default_factories() -> Dict[str, Callable[[], Backend]]:
    from sethlans_worker_agent.gpu_memory import nvidia_smi
    return {nvidia_smi.SOURCE: nvidia_smi.NvidiaSmiBackend, 'pdh': _pdh_backend}


def _configured_source() -> str:
    from sethlans_worker_agent import config_gpu_memory
    return config_gpu_memory.SOURCE


def candidates(setting: str, platform: Optional[str] = None, is_64bit: Optional[bool] = None) -> Tuple[str, ...]:
    """Backend names to try, in order, for a ``[gpu_memory] source`` value (FR-GS-2)."""
    if setting == SOURCE_NONE:
        return ()
    if setting != 'auto':
        return (setting,)
    platform = sys.platform if platform is None else platform
    if platform == 'win32':
        is_64bit = sys.maxsize > 2 ** 32 if is_64bit is None else is_64bit
        return ('pdh', 'nvidia-smi') if is_64bit else ('nvidia-smi',)
    if platform.startswith('linux'):
        return ('nvidia-smi',)
    return ()


def _percentile(ordered, fraction: float) -> float:
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _card_list(reading: GpuMemoryReading) -> str:
    return '; '.join(f"{gpu.index} '{gpu.name}' {gpu.total_mb} MiB {gpu.card_key or 'no card key'}"
                     for gpu in reading.gpus)


class GpuMemoryReader:

    def __init__(self, setting: Optional[str] = None, platform: Optional[str] = None,
                 factories: Optional[Dict[str, Callable[[], Backend]]] = None,
                 clock: Callable[[], float] = time.monotonic):
        self._setting, self._platform, self._factories, self._clock = setting, platform, factories, clock
        self._sample_lock = threading.Lock()  # leaf lock
        self._backend: Optional[Backend] = None
        self._source = ''
        self._next_select: Optional[float] = None
        self._durations: deque = deque(maxlen=STATS_WINDOW)
        self._stats_at = 0.0
        self._error_logged = self._invalid_logged = False

    @property
    def source(self) -> str:
        """The chosen backend's name, ``'none'``, or ``''`` before the first selection."""
        return self._source

    def read(self, pids: Callable[[], frozenset]) -> Optional[GpuMemoryReading]:
        """One reading, or None. Never raises."""
        try:
            with self._sample_lock:
                return self._checked(self._read_locked(pids))
        except Exception:  # noqa: BLE001 - "any failure yields None"
            if not self._error_logged:
                self._error_logged = True
                logger.exception("GPU memory reading failed.")
            else:
                logger.debug("GPU memory reading failed.", exc_info=True)
            return None

    def close(self) -> None:
        """Close the chosen backend (waits for a running sample) and forget the selection."""
        with self._sample_lock:
            backend, self._backend = self._backend, None
            self._source, self._next_select = '', None
            self._close_quietly(backend)

    # -- under _sample_lock -----------------------------------------------
    def _read_locked(self, pids) -> Optional[GpuMemoryReading]:
        if self._backend is not None:
            self._before_sample(self._backend)
            return self._timed(self._backend, pids)
        if self._next_select is not None and self._clock() < self._next_select:
            return None
        return self._select(pids)

    @staticmethod
    def _before_sample(backend: Backend) -> None:
        """A chosen backend's optional, untimed ``before_sample()`` (log-only work); a failure is ignored."""
        hook = getattr(backend, 'before_sample', None)
        if hook is None:
            return
        try:
            hook()
        except Exception:  # noqa: BLE001 - log-only work never costs a reading
            logger.debug("GPU memory source %s: work before the sample failed.", backend.name, exc_info=True)

    def _timed(self, backend: Backend, pids) -> Optional[GpuMemoryReading]:
        ours = pids()
        started = self._clock()
        try:
            return backend.sample(ours)
        finally:
            self._record(self._clock() - started)

    def _select(self, pids) -> Optional[GpuMemoryReading]:
        names = candidates(self._setting or _configured_source(), self._platform)
        factories = self._factories if self._factories is not None else _default_factories()
        for name in names:
            first, reason = self._try(name, factories.get(name), pids)
            if first is not None:
                return first
            logger.info("GPU memory source %s not chosen: %s.", name, reason)
        self._source, self._next_select = SOURCE_NONE, self._clock() + RESELECT_SECONDS
        logger.info("No GPU memory source was chosen (tried: %s); the next try is in %d s.",
                    ', '.join(names) or 'none on this platform', RESELECT_SECONDS)
        return None

    def _try(self, name: str, factory, pids) -> Tuple[Optional[GpuMemoryReading], str]:
        if factory is None:
            return None, "not available on this worker"
        backend = None
        try:
            backend = factory()
            if not backend.open():
                reason = "it cannot be used here"
            else:
                first = self._timed(backend, pids)
                if first is not None and first.gpus:
                    logger.info("GPU memory source: %s; cards: %s.", name, _card_list(first))
                    self._stats_at = self._clock()
                    self._backend, self._source = backend, name  # last: a failure above closes it unchosen
                    return first, ''
                reason = "its first sample has no card"
        except Exception as exc:  # noqa: BLE001 - a broken candidate is skipped
            logger.debug("GPU memory source %s failed.", name, exc_info=True)
            reason = f"it failed ({type(exc).__name__})"
        self._close_quietly(backend)
        return None, reason

    @staticmethod
    def _close_quietly(backend: Optional[Backend]) -> None:
        if backend is None:
            return
        try:
            backend.close()
        except Exception:  # noqa: BLE001 - a failing close never stops the reader
            logger.warning("Closing the GPU memory source %s failed.", getattr(backend, 'name', '?'), exc_info=True)

    def _checked(self, reading: Optional[GpuMemoryReading]) -> Optional[GpuMemoryReading]:
        if reading is None or not reading.gpus:
            return None
        text = problem(reading)
        if text:
            if not self._invalid_logged:
                self._invalid_logged = True
                logger.warning("GPU memory reading from %s dropped: %s.", reading.source, text)
            return None
        return reading

    def _record(self, seconds: float) -> None:
        self._durations.append(seconds)
        now = self._clock()
        if self._backend is None or now - self._stats_at < STATS_SECONDS:
            return
        self._stats_at = now
        ordered = sorted(self._durations)
        logger.info("GPU memory samples (%s): n=%d, p50=%.1f, p95=%.1f, max=%.1f", self._source, len(ordered),
                    1000 * _percentile(ordered, 0.5), 1000 * _percentile(ordered, 0.95), 1000 * ordered[-1])


_singleton_lock = threading.Lock()  # leaf lock
_reader: Optional[GpuMemoryReader] = None


def get_reader() -> GpuMemoryReader:
    """The process-wide reader, created on the first call."""
    global _reader
    with _singleton_lock:
        if _reader is None:
            _reader = GpuMemoryReader()
        return _reader


def reset_for_tests() -> None:
    """Close and forget the singleton (test isolation only)."""
    global _reader
    with _singleton_lock:
        old, _reader = _reader, None
    if old is not None:
        old.close()
