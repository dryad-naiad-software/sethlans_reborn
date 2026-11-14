# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared fakes for the executor-interface tests (Unreal foundation chunk 3).

The real Unreal executor lands in chunk 7. ``FakeUnrealExecutor`` has the
shape the spec gives it: ``capacity_mode='exclusive'``, a
``BoundedSemaphore(1)`` token acquired non-blocking in ``try_reserve``,
and a ``try_reserve`` that rejects unless the machine is fully idle
(FR-UE-1 check 0). ``install_fake_unreal`` makes the real lazy import in
``executors.get_executor('UNREAL')`` find it.

Module name starts with an underscore so pytest does not collect it.
"""
import sys
import threading
import types

from sethlans_worker_agent import executors, job_processor, job_selection, unreal_bridge
from sethlans_worker_agent.capacity import CapacityProfile, WorkerCapacity
from sethlans_worker_agent.executors import ProbePending, RendererReservation, RenderResult

UNREAL_MODULE = 'sethlans_worker_agent.unreal_executor.executor'


def make_result(**overrides):
    fields = dict(
        success=True, was_canceled=False, was_yielded=False,
        stdout='', stderr='', error_message='', output_path=None,
        thumbnail_path=None, yield_monitor=None, grace_outcome=None,
        render_time_seconds=7, released=False,
    )
    fields.update(overrides)
    return RenderResult(**fields)


class FakeUnrealExecutor:
    renderer = 'UNREAL'
    capacity_mode = 'exclusive'

    def __init__(self):
        self.token = threading.BoundedSemaphore(1)
        self.pending_ids = set()
        self.eligible = True
        self.retain = False
        self.result = make_result()
        self.events = []

    def try_reserve(self, job_data):
        if job_data.get('id') in self.pending_ids:
            raise ProbePending()
        if not self.eligible:
            return None
        capacity = job_processor.get_worker_capacity()
        if capacity is not None and not capacity.has_no_reservations():
            return None
        if not self.token.acquire(blocking=False):
            return None
        return RendererReservation(on_release=self._release_token)

    def _release_token(self):
        self.events.append('token_released')
        self.token.release()

    def execute(self, job_data, ctx):
        self.events.append('execute')
        if self.retain:
            job_data['_renderer_reservation'].retain_until_restart()
        return self.result

    def token_free(self):
        """True when the token can be taken (and puts it back)."""
        if self.token.acquire(blocking=False):
            self.token.release()
            return True
        return False


def install_fake_unreal(monkeypatch):
    """Make ``get_executor('UNREAL')`` return a fresh fake."""
    fake = FakeUnrealExecutor()
    module = types.ModuleType(UNREAL_MODULE)
    module.UnrealExecutor = lambda: fake
    monkeypatch.setitem(sys.modules, UNREAL_MODULE, module)
    monkeypatch.setattr(executors, '_unreal_executor', None)
    monkeypatch.setattr(executors, '_unreal_load_error', None)
    return fake


def profile(gpu_slot_count=1, cpu_slot_count=1, startup_gpu_count=None,
            gpu_mode='split', force_gpu_index=None):
    return CapacityProfile(
        gpu_slot_count=gpu_slot_count, cpu_slot_count=cpu_slot_count,
        cpu_thread_ceiling=15, cpu_threads_effective=15,
        startup_gpu_count=(gpu_slot_count if startup_gpu_count is None
                           else startup_gpu_count),
        gpu_mode=gpu_mode, force_gpu_index=force_gpu_index,
    )


def install_capacity(**kwargs):
    cap = WorkerCapacity(profile(**kwargs))
    job_processor._capacity = cap
    return cap


def blender_job(job_id=101, device='GPU', **extra):
    job = {'id': job_id, 'name': f'B-{job_id}', 'render_device': device,
           'render_engine': 'CYCLES'}
    job.update(extra)
    return job


def unreal_job(job_id=201, **extra):
    job = {'id': job_id, 'name': f'U-{job_id}', 'renderer': 'UNREAL',
           'render_device': 'GPU', 'render_engine': ''}
    job.update(extra)
    return job


def reset_chunk3_state():
    """Clear module state the pre-existing conftests do not know about."""
    with job_processor._active_jobs_lock:
        job_processor._unfinished_job_ids.clear()
    job_selection.reset_for_tests()
    executors._unreal_executor = None
    executors._unreal_load_error = None
    with unreal_bridge._failures_lock:
        unreal_bridge._failures_logged.clear()


def fail_imports(monkeypatch, *targets):
    """Make importing any of ``targets`` (dotted names) raise ImportError."""
    import builtins
    real_import = builtins.__import__

    def _hits(name, fromlist):
        names = [name] + [f'{name}.{item}' for item in (fromlist or ())]
        return any(n == t or n.startswith(t + '.') for n in names for t in targets)

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and _hits(name, fromlist):
            raise ImportError(f'forced failure importing {name}')
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', guarded)
