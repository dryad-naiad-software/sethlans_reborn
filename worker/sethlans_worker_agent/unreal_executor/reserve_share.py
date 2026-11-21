# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Reserver checks 7 and 8: the share state and the FR-PA-3 probes (Unreal foundation FR-UE-1).

Moved out of ``reserve.py`` (300-line limit). ``deps`` is the ``ReserveDeps``; ``skip(job_id,
message, *args)`` is the reserver's rate-limited skip log.

* ``share(params, deps, skip)``: one read of the worker's share state (a configured, reachable
  root, copied into ``JobParams.share_root``); the archive and overlay paths must resolve inside it
  (string-only). Returns the params with the root, or ``None``.
* ``probes(params, deps, skip)``: the archive (unless a ``ready`` cache entry exists) and the
  overlay, on the ``poll`` pool within the per-call probe budget. ``True``, ``False``, or ``None``
  while a probe is pending.
"""
from typing import Callable, Optional


def share(params, deps, skip: Callable):
    state = deps.share_state()
    if state.root is None or not state.reachable:
        skip(params.job_id, "the Sethlans share is not reachable.")
        return None
    params = params.with_share_root(state.root)
    try:
        deps.resolve(state.root, params.archive_path, 'file')
        if params.overlay_path:
            deps.resolve(state.root, params.overlay_path, 'dir')
    except ValueError as exc:
        skip(params.job_id, "share path rejected (%s).", exc)
        return None
    return params


def probes(params, deps, skip: Callable) -> Optional[bool]:
    """True, False, or None (pending)."""
    reach, budget = deps.reachability(), deps.budget()
    checks = []
    if not deps.entry_ready(params.archive_sha256):
        checks.append((params.archive_path, 'file'))
    if params.overlay_path:
        checks.append((params.overlay_path, 'dir'))
    pending = False
    for relpath, kind in checks:
        result = reach.check(params.share_root, relpath, kind, job_id=params.job_id, pool='poll', budget=budget)
        if result is None:
            pending = True
        elif not result:
            skip(params.job_id, "%s is not reachable on the share.", relpath)
            return False
    return None if pending else True
