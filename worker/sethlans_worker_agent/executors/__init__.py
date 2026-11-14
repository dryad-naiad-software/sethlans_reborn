# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Renderer executor registry (Unreal foundation FR-WX-1).

``get_executor(renderer)`` returns the executor for a job's ``renderer``.
Callers apply the default themselves: ``job.get('renderer') or 'BLENDER'``
(a missing key or a JSON ``null`` means Blender, for older managers).

Import isolation: ``BlenderExecutor`` is imported here at module level;
``UnrealExecutor`` is imported lazily, on the first ``'UNREAL'`` lookup,
with a plain ``from ... import`` statement that PyInstaller still detects.
If that import fails, the lookup raises ``UnsupportedRendererError`` and
Blender processing is unaffected. The failure is cached: later lookups
raise at once and do not import again (a worker restart retries).

``UnrealExecutor.__init__`` runs under ``_unreal_lock``: it must be
lock-free (take no other worker lock) and start no threads.
"""
import logging
import threading

from sethlans_worker_agent.executors.base import (  # noqa: F401
    ExecutionContext,
    ProbePending,
    RenderExecutor,
    RendererReservation,
    RenderResult,
    UnsupportedRendererError,
)
from sethlans_worker_agent.executors.blender import BlenderExecutor

logger = logging.getLogger(__name__)

DEFAULT_RENDERER = 'BLENDER'

# Renderers whose jobs the worker hands back to the manager (release/)
# instead of PATCHing CANCELED or leaving them to the reaper: the drift
# handler and the thread-start failure path use this (FR-WX-7, FR-WX-14).
_RELEASABLE_RENDERERS = frozenset({'UNREAL'})

_blender_executor = BlenderExecutor()

# Lazily built singleton: its renderer token must be shared by every
# lookup. Guarded by _unreal_lock (a leaf lock; the poll thread and job
# threads both call get_executor).
_unreal_executor = None
_unreal_load_error = None  # first import/construction failure, cached
_unreal_lock = threading.Lock()


def _load_unreal_executor():
    """Return the Unreal executor, importing it on first use."""
    global _unreal_executor, _unreal_load_error
    with _unreal_lock:
        if _unreal_executor is not None:
            return _unreal_executor
        if _unreal_load_error is not None:
            raise UnsupportedRendererError('UNREAL') from _unreal_load_error
        try:
            from sethlans_worker_agent.unreal_executor.executor import UnrealExecutor
            _unreal_executor = UnrealExecutor()
        except Exception as exc:
            _unreal_load_error = exc
            logger.error(
                "Unreal executor failed to load (%s: %s); Unreal jobs "
                "are skipped on this worker until it restarts.",
                type(exc).__name__, exc,
            )
            raise UnsupportedRendererError('UNREAL') from exc
        return _unreal_executor


def get_executor(renderer: str) -> RenderExecutor:
    """Return the executor for ``renderer``.

    Raises ``UnsupportedRendererError`` for an unknown value or when the
    Unreal executor cannot be loaded.
    """
    if renderer == 'BLENDER':
        return _blender_executor
    if renderer == 'UNREAL':
        return _load_unreal_executor()
    raise UnsupportedRendererError(renderer)


def supports_release(renderer) -> bool:
    """True when jobs of ``renderer`` are released to the manager (Unreal)."""
    return (renderer or DEFAULT_RENDERER) in _RELEASABLE_RENDERERS
