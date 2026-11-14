# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The one pre-launch check shared by every UE phase (Unreal foundation
chunk 7 integration requirement 15; FR-UE-26, FR-UE-27; Conc R3-M6).

``check_before_launch`` runs immediately before every UE launch (prep,
warm-up, render, retry), in this order:

1. ``user_state.repair_profile``, **always**: ``P`` and every pre-created
   folder are ``lstat``-, reparse- and owner-checked; a link or non-folder
   there is moved to ``trash/`` (as a link, never followed) and re-created
   (review S4); a foreign owner fails the launch. Then ``zen/data`` and
   ``zen/fs`` are created or checked (FR-UE-27, FR-UE-26);
2. before the job's **first** launch only: the known-folder check
   (``isolation_state.check_now``, one probe per job);
3. the farm-profile config check (``user_state.scan_farm_profile_config``),
   which resets contaminated layers and lets the launch proceed;
4. the farm Zen port re-check (``zen_port.check_port``, any address).

A failure is a **local conflict**, never a job ERROR: the result names the
release reason, ``dispatch_failed`` before the job's first launch and
``local_conflict`` after it. The executor (chunk 7b) calls
``api_extras.release_job(job_id, reason)`` with it (one attempt, 5 s) and
returns ``released=True``. Nothing here talks to the manager.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

from sethlans_worker_agent.unreal_executor import isolation_state, user_state, zen_port, zen_private

DISPATCH_FAILED = 'dispatch_failed'
LOCAL_CONFLICT = 'local_conflict'


@dataclass(frozen=True)
class PreLaunch:
    ok: bool
    release_reason: str = ''
    message: str = ''
    profile_reset: int = 0
    profile_repaired: int = 0
    user_cvars: int = 0
    user_cvar_collisions: Tuple[str, ...] = ()


def _fail(first_launch: bool, message: str) -> PreLaunch:
    return PreLaunch(ok=False, release_reason=DISPATCH_FAILED if first_launch else LOCAL_CONFLICT, message=message)


def check_before_launch(cache_dir, port: int, merged_names: Iterable[str], first_launch: bool,
                        worker_sid: Optional[str] = None,
                        known_folders: Callable = isolation_state.check_now,
                        config_check: Callable = user_state.scan_farm_profile_config,
                        port_check: Callable = zen_port.check_port) -> PreLaunch:
    """Run the shared pre-launch checks; never raises."""
    profile = Path(user_state.profile_dir(cache_dir))
    trash = Path(cache_dir) / 'trash'
    try:
        repaired = user_state.repair_profile(profile, trash)
        zen_private.ensure_zen_dirs(cache_dir)
    except OSError as exc:
        return _fail(first_launch, f"The farm profile or Zen folders are unusable ({exc}).")
    if first_launch:
        result = known_folders()
        if result is None or not result.ok:
            reason = result.reason if result is not None else "the known-folder check has not run"
            return _fail(True, reason)
    config = config_check(profile, trash, merged_names)
    if not config.ok:
        return _fail(first_launch, f"The farm profile's config could not be reset ({config.error}).")
    ports = port_check(port, cache_dir, worker_sid=worker_sid)
    if not ports.ok:
        return _fail(first_launch, ports.message)
    return PreLaunch(ok=True, profile_reset=config.reset, profile_repaired=repaired, user_cvars=config.user_cvars,
                     user_cvar_collisions=tuple(config.collisions))
