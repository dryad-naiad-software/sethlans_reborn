# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Renderer executor interface (Unreal foundation FR-WX-1).

Every renderer the worker can run is reached through a ``RenderExecutor``.
The job lifecycle only sees ``RenderResult``; the claim path only sees
``try_reserve`` and ``capacity_mode``.

``RendererReservation`` is a resource token handed out by ``try_reserve``
and released by the claim path or the job lifecycle. Its internal lock is
a leaf lock (FR-WX-9): no other lock is ever acquired while it is held,
and the release callback runs after it is dropped.
"""
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional, Protocol

logger = logging.getLogger(__name__)

CapacityMode = Literal['shared', 'exclusive']


class UnsupportedRendererError(Exception):
    """No executor for this renderer on this worker (FR-WX-1, FR-WX-5).

    The message is the exact text the job thread reports as the job's
    ``error_message``.
    """

    def __init__(self, renderer: Any):
        self.renderer = renderer
        super().__init__(f"Unsupported renderer '{renderer}' on this worker.")


class ProbePending(Exception):
    """Raised by ``try_reserve`` when the only reason it declines a job is
    a share probe that has not finished yet (FR-WX-6, FR-PA-3).

    ``job_selection`` then waits one poll cycle for the first job in
    response order, at most once per job id. A probe that has a result,
    positive or negative, never raises this.

    Contract: ``try_reserve`` must not hold any renderer resource (token,
    reservation) when it raises or returns None.
    """


@dataclass(frozen=True)
class ExecutionContext:
    """Per-job inputs the lifecycle hands to ``RenderExecutor.execute``."""
    assigned_gpu_index: Optional[int]
    yield_monitor_factory: Optional[Callable]
    manual_stop_event: Optional[threading.Event]
    shutdown_event: Optional[threading.Event]


@dataclass(frozen=True)
class RenderResult:
    """Outcome of one render, renderer independent."""
    success: bool
    was_canceled: bool
    was_yielded: bool
    stdout: str
    stderr: str
    error_message: str
    output_path: Optional[str]
    thumbnail_path: Optional[str]
    yield_monitor: Any
    grace_outcome: Optional[str]
    render_time_seconds: Optional[int] = None
    # The executor already handed the job back to the manager (or
    # abandoned it after reassignment): the lifecycle skips the status
    # report and the upload. Blender never sets it.
    released: bool = False


class RendererReservation:
    """Idempotent renderer-side reservation (FR-WX-4, FR-WX-9).

    ``release()`` runs ``on_release`` at most once. After
    ``retain_until_restart()`` it is a no-op for the rest of the process:
    the lifecycle then keeps both this reservation and the capacity slots,
    so the machine takes no further work until the worker restarts
    (FR-UE-11 survivors). Blender's reservation has no callback and is
    never retained.

    Only the job thread that runs the job may call
    ``retain_until_restart()`` (from inside ``execute``). ``release()``
    reports the outcome atomically, so callers decide about the capacity
    slot from its return value rather than from a separate ``retained``
    read.
    """

    def __init__(self, on_release: Optional[Callable[[], None]] = None):
        self._lock = threading.Lock()  # leaf lock
        self._released = False
        self._retained = False
        self._on_release = on_release

    @property
    def released(self) -> bool:
        with self._lock:
            return self._released

    @property
    def retained(self) -> bool:
        with self._lock:
            return self._retained

    def retain_until_restart(self) -> None:
        """Keep the reservation (and the job's slots) until restart."""
        with self._lock:
            if not self._released:
                self._retained = True

    def release(self) -> bool:
        """Release once. Never raises.

        Returns False while retained (nothing was released), True when the
        reservation is released, by this call or an earlier one.
        """
        with self._lock:
            if self._retained:
                return False
            if self._released:
                return True
            self._released = True
            callback = self._on_release
        if callback is not None:
            try:
                callback()
            except Exception:  # noqa: BLE001 - called from finally blocks
                logger.exception("Renderer reservation release callback failed.")
        return True


class RenderExecutor(Protocol):
    """What the claim path and the lifecycle need from a renderer."""
    renderer: str
    # 'shared': one capacity slot per job (Blender).
    # 'exclusive': the whole machine (Unreal, D1).
    capacity_mode: CapacityMode

    def try_reserve(self, job_data: dict) -> Optional[RendererReservation]:
        """Return a reservation if this job is eligible now, else None.

        May raise ``ProbePending`` (see that class). Must not hold any
        renderer resource (token, reservation) when it raises or returns
        None.
        """
        ...

    def execute(self, job_data: dict, ctx: ExecutionContext) -> RenderResult:
        """Run the job to completion and describe the outcome."""
        ...
