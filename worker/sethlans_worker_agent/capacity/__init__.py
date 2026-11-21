# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker capacity computation, slot reservation, card checks and the live rebuild.

The single owner of "how full is this worker?" logic. Constructed once
at worker startup from the saved card choice. A change of the cards in use
(a lost card, a dismissed alert, a saved choice) no longer ends the worker:
the jobs on affected cards are stopped one by one and the profile is
rebuilt once no running job blocks it (FR-CL-4..6).

Lock ordering: WorkerCapacity._lock is acquired and released entirely
within each public method; below it only ``rebuild._lock``, the innermost
lock of the worker. Never held across an API call or a Blender subprocess
invocation.

Documented order (enforced by convention and by test_capacity.py):
    WorkerCapacity._lock < _active_jobs_lock < _recent_jobs_lock
hardware_detection._gpu_details_lock is taken with no worker lock held
and nests only tool_manager._download_lock (a leaf).

Package layout:
    profile.py    -- CapacityProfile, compute_capacity_profile,
                     cpu_threads_for_blender, SlotReservation.
    drift.py      -- detect_physical_gpus_now (uncached GPU list from the
                     Blender that produced the list in use).
    card_check.py -- the start and periodic card checks (FR-CK-1..3), and
                     the OS-only check during an Unreal render (C-24 m).
    card_stops.py -- the alerts and per-job stops of one evaluation.
    rebuild.py    -- generation, pending mark and profile swap (FR-CL-4).
    recheck.py    -- when the check runs: periodically, and early (with a
                     floor) after a failed GPU device check.
    gpu_list.py   -- (device id, backend) pairs of a GPU list (#239).
    slots.py      -- WorkerCapacity stateful slot tracker.
    unreal_hold.py -- the per-card Unreal hold (one card key per Unreal job,
                     by PCI address; the CPU rule and the shared mark),
                     used by WorkerCapacity.reserve_unreal_card and friends.
"""
from sethlans_worker_agent.capacity.profile import (  # noqa: F401
    CapacityProfile,
    SlotReservation,
    compute_capacity_profile,
    cpu_threads_for_blender,
    log_capacity_profile,
)
from sethlans_worker_agent.capacity.drift import (  # noqa: F401
    detect_physical_gpus_now,
    reset_missing_executable_warning,
)
from sethlans_worker_agent.capacity.rebuild import (  # noqa: F401
    rebuild_waiting,
    request_rebuild,
)
from sethlans_worker_agent.capacity.recheck import (  # noqa: F401
    IMMEDIATE_RECHECK_FLOOR_SECONDS,
    gpu_list_recheck_pending,
    request_gpu_list_recheck,
    reset_gpu_list_recheck,
    run_gpu_list_check,
    take_gpu_list_check_turn,
)
from sethlans_worker_agent.capacity.slots import WorkerCapacity  # noqa: F401
