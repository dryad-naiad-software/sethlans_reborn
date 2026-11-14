# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Share reachability of archive and overlay paths (Unreal foundation
FR-PA-3, D3b/Q3, Conc R2-M2c, Sec N5, Dom N-16).

Every share path is ``resolve_share_path(job_params.share_root, relpath,
kind)``: the root is the worker's own configured root captured at
``try_reserve``; the relpath is re-validated (FR-MU-15) and the result
re-validated (FR-MU-6) and checked to lie within that same root. No other
UNC path is ever touched for project data.

``Reachability.check(share_root, relpath, kind, ...)`` is True only when:

* the worker's share state (``unreal_share.state()``) has a root and is
  reachable;
* the path resolves inside the root;
* the **host** ``\\\\host\\share`` answers (cached per ``(host, share)``:
  10 s when positive, 60 s when negative);
* a per-path ``os.stat(path, follow_symlinks=False)`` returns within
  ``SHARE_TIMEOUT_SECONDS`` and shows a regular file (``'file'``) or a
  directory (``'dir'``) that is not a reparse point. A positive per-path
  result is never cached; a negative one is cached 60 s per
  ``(job_id, relpath)``.

Stats run on two dedicated 2-thread daemon pools: ``'poll'`` for the poll
loop (``try_reserve``) and ``'job'`` for job threads, so a stuck poll probe
never causes job timeouts. With a ``ProbeBudget`` the call returns
``None`` (pending) once the budget is spent; the host probe keeps running
and its result is cached for a later poll, while a per-path probe that has
not started is cancelled. At most one host probe per ``(host, share)`` is
in flight (a pending one is reused), and a result is applied only if its
probe started after the cached one (Conc C5). Pools start lazily (NF-6).
"""
import os
import queue
import stat
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

from sethlans_worker_agent.project_archive import fs_safe
from shared.unreal.unc import UncPath, validate_unc_dir

HOST_POSITIVE_TTL = 10.0
HOST_NEGATIVE_TTL = 60.0
PATH_NEGATIVE_TTL = 60.0
POOL_SIZE = 2


def resolve_share_path(root: UncPath, relpath: str, kind: str) -> UncPath:
    """FR-MU-15 + FR-MU-6 + containment in ``root``; raises ``ValueError``."""
    from shared.unreal.share_paths import resolve_in_share, validate_share_relpath  # chunk 2
    validate_share_relpath(relpath, kind=kind, suffix='.zip' if kind == 'file' else None)
    resolved = resolve_in_share(root, relpath)
    if not resolved.is_within(root):
        raise ValueError("path resolves outside the Sethlans share")
    return resolved


def _default_state():
    from sethlans_worker_agent import unreal_share
    return unreal_share.state()


@dataclass
class ProbeBudget:
    """Total probe time for one poll cycle (``POLL_PROBE_BUDGET_SECONDS``)."""
    seconds: float
    clock: Callable[[], float] = time.monotonic
    started: float = field(default=-1.0)

    def __post_init__(self):
        if self.started < 0:
            self.started = self.clock()

    def remaining(self) -> float:
        return max(self.seconds - (self.clock() - self.started), 0.0)


class _DaemonPool:
    """A tiny pool of daemon threads (a blocked SMB stat never holds up exit)."""

    def __init__(self, size: int, name: str):
        self._queue: 'queue.SimpleQueue' = queue.SimpleQueue()
        for index in range(size):
            threading.Thread(target=self._run, name=f'{name}-{index}', daemon=True).start()

    def submit(self, fn, *args) -> Future:
        future: Future = Future()
        self._queue.put((future, fn, args))
        return future

    def _run(self) -> None:
        while True:
            future, fn, args = self._queue.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn(*args))
            except BaseException as exc:  # noqa: BLE001 - delivered through the future
                future.set_exception(exc)


class Reachability:

    def __init__(self, timeout_seconds: float, *, state_fn: Callable = _default_state,
                 resolver: Callable = resolve_share_path, stat_fn: Callable = os.stat,
                 clock: Callable[[], float] = time.monotonic):
        self._timeout = timeout_seconds
        self._state_fn = state_fn
        self._resolver = resolver
        self._stat = stat_fn
        self._clock = clock
        self._lock = threading.Lock()  # leaf lock
        self._hosts: Dict[Tuple[str, str], Tuple[bool, float, float]] = {}  # ok, expires, started
        # keyed by (pool, host, share): a stuck poll-pool probe must never make a
        # job-pool check wait behind it (FR-PA-3); the result cache stays shared
        self._inflight: Dict[Tuple[str, str, str], Future] = {}
        self._negative: Dict[Tuple[Optional[int], str], float] = {}
        self._pools: Dict[str, _DaemonPool] = {}

    def _pool(self, name: str) -> _DaemonPool:
        with self._lock:
            pool = self._pools.get(name)
            if pool is None:
                pool = self._pools[name] = _DaemonPool(POOL_SIZE, f'unreal-probe-{name}')
            return pool

    def _stat_kind(self, path: str, kind: str) -> bool:
        st = self._stat(path, follow_symlinks=False)
        if fs_safe.is_reparse(st):
            return False
        return stat.S_ISREG(st.st_mode) if kind == 'file' else stat.S_ISDIR(st.st_mode)

    def _probe(self, path: str, kind: str) -> bool:
        try:
            return self._stat_kind(path, kind)
        except OSError:
            return False

    def _host_probe(self, host_key: Tuple[str, str], root: str, started: float) -> bool:
        ok = self._probe(root, 'dir')
        with self._lock:
            cached = self._hosts.get(host_key)
            if cached is None or cached[2] < started:  # never let an older probe overwrite a newer one
                ttl = HOST_POSITIVE_TTL if ok else HOST_NEGATIVE_TTL
                self._hosts[host_key] = (ok, self._clock() + ttl, started)
        return ok

    def _cached_host(self, host_key) -> Optional[bool]:
        with self._lock:
            cached = self._hosts.get(host_key)
        if cached is None or self._clock() >= cached[1]:
            return None
        return cached[0]

    def _host_future(self, pool: str, host_key: Tuple[str, str], root: str) -> Future:
        """The pending host probe for ``host_key`` in ``pool``, or a new one (one in flight per pool)."""
        inflight_key = (pool,) + tuple(host_key)
        with self._lock:
            pending = self._inflight.get(inflight_key)
            if pending is not None and not pending.done():
                return pending
        future = self._pool(pool).submit(self._host_probe, host_key, root, self._clock())
        with self._lock:
            self._inflight[inflight_key] = future
        future.add_done_callback(lambda done, key=inflight_key: self._forget_inflight(key, done))
        return future

    def _forget_inflight(self, inflight_key, future) -> None:
        with self._lock:
            if self._inflight.get(inflight_key) is future:
                del self._inflight[inflight_key]

    def _wait(self, future: Future, budget: Optional[ProbeBudget]) -> Optional[bool]:
        limit = self._timeout if budget is None else min(self._timeout, budget.remaining())
        budget_capped = budget is not None and limit < self._timeout
        try:
            return bool(future.result(timeout=limit))
        except FutureTimeout:
            # Pending when the poll budget, not the probe timeout, cut the wait short. Re-reading
            # the budget here is unreliable: the wait can wake a timer tick early (#231).
            return None if budget_capped else False
        except Exception:  # noqa: BLE001 - a failed probe is a negative result
            return False

    def _negative_cached(self, key) -> bool:
        with self._lock:
            expires = self._negative.get(key)
        return expires is not None and self._clock() < expires

    def _remember_negative(self, key) -> None:
        with self._lock:
            self._negative[key] = self._clock() + PATH_NEGATIVE_TTL
            for stale in [k for k, exp in self._negative.items() if exp <= self._clock()]:
                del self._negative[stale]

    def check(self, share_root: UncPath, relpath: str, kind: str, *, job_id: Optional[int] = None,
              pool: str = 'job', budget: Optional[ProbeBudget] = None) -> Optional[bool]:
        """True, False, or None (pending: the poll budget ran out). See the module docstring."""
        state = self._state_fn()
        if share_root is None or state.root is None or not state.reachable:
            return False
        try:
            resolved = self._resolver(share_root, relpath, kind)
        except ValueError:
            return False
        key = (job_id, relpath)
        if self._negative_cached(key):
            return False
        host_key = (resolved.host.lower(), resolved.share.lower())
        host_ok = self._cached_host(host_key)
        if host_ok is None:
            root = validate_unc_dir(f'\\\\{resolved.host}\\{resolved.share}').canonical
            host_ok = self._wait(self._host_future(pool, host_key, root), budget)
            if host_ok is None:
                return None
        if not host_ok:
            return False
        future = self._pool(pool).submit(self._probe, resolved.canonical, kind)
        result = self._wait(future, budget)
        if result is None:
            future.cancel()  # budget spent: a probe that has not started is dropped
        if result is False:
            self._remember_negative(key)
        return result
