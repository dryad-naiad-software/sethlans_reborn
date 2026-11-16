# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker capacity computation, slot reservation, and GPU drift detection.

The single owner of "how full is this worker?" logic. Constructed once
at worker startup with a frozen view of the hardware. The total capacity
is fixed for the worker's lifetime; GPU dropout is a hard error that
triggers graceful shutdown via agent._shutdown_event.

Lock ordering: WorkerCapacity._lock is the innermost lock in the worker.
It is acquired and released entirely within each public method. Never
held across an API call or a Blender subprocess invocation.

Documented order (enforced by convention and by test_capacity.py):
    WorkerCapacity._lock < _active_jobs_lock < _recent_jobs_lock

Package layout:
    profile.py  -- CapacityProfile, compute_capacity_profile,
                   cpu_threads_for_blender, SlotReservation.
    drift.py    -- detect_physical_gpus_now (uncached GPU list from the
                   Blender that produced the list in use) and the
                   module-level _drift_detected_exit_code flag.
    recheck.py  -- when the GPU list comparison runs: periodically, and
                   early (with a floor) after a failed GPU device check.
    gpu_list.py -- (device id, backend) identity of a GPU list and the
                   description of how two lists differ (#239).
    slots.py    -- WorkerCapacity stateful slot tracker.
    exclusive.py -- whole-machine (Unreal) reservation helpers used by
                   WorkerCapacity.reserve_exclusive / has_no_reservations.
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
    get_drift_exit_code,
    reset_drift_exit_code,
    reset_missing_executable_warning,
    set_drift_exit_code,
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


# Re-export the module-level drift flag name for backward-compatible
# access (`capacity._drift_detected_exit_code`) used by the agent epilogue
# and by unit tests that monkey-patch the flag.
def __getattr__(name):  # pragma: no cover - simple shim
    if name == '_drift_detected_exit_code':
        from sethlans_worker_agent.capacity import drift
        return drift._drift_detected_exit_code
    raise AttributeError(name)
