# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Helpers for the Unreal-foundation chunk-4 worker tests (config, share,
detection, GPU memory, capabilities).

``unreal_runtime_reset`` is an autouse-able fixture: import it into a
test module (``from tests._unreal_worker_state import
unreal_runtime_reset  # noqa: F401``) to stop the share/VRAM threads and
forget the startup result before and after every test.

``enable_unreal`` turns Unreal on for one test **without** touching the
real machine: the cache dir, owner lock, symlink policy and threads are
replaced by fakes, and detection returns the given installs.
"""
from pathlib import Path

import pytest

from sethlans_worker_agent import config_unreal, unreal_share
from sethlans_worker_agent.unreal_executor import cache_dir, detection, runtime, startup_recovery
from sethlans_worker_agent.unreal_executor.vram import GpuMem, GpuMemoryReading

BUILD_58 = '5.8.4-58902052'
BUILD_57 = '5.7.4-51494982'


def install(build=BUILD_58, supported=True, source='launcher', reason=''):
    root = Path('C:/Program Files/Epic Games') / f'UE_{build or "source"}'
    return detection.EngineInstall(
        root=root, editor_cmd=root / 'Engine/Binaries/Win64/UnrealEditor-Cmd.exe',
        build=build, supported=supported, reason=reason, source=source)


def reading(total=32607, used=1012, sethlans=None, other=None, taken_at=0.0, source='nvidia-smi'):
    return GpuMemoryReading(taken_at, source, (GpuMem(0, 'NVIDIA GeForce RTX 5090', total, used, sethlans, other),))


class FakeSampler:
    def __init__(self, value=None, never_succeeded=False):
        self.value = value
        self._never = never_succeeded

    def never_succeeded(self):
        return self._never

    def latest(self):
        return self.value

    def stop(self, timeout=None):
        pass


@pytest.fixture(autouse=False)
def unreal_runtime_reset():
    runtime.reset_for_tests()
    yield
    runtime.reset_for_tests()


def reachable(root='\\\\nas.lan\\sethlans', ok=True):
    """A ``ShareState`` for ``root`` as a completed check reports it."""
    parsed = unreal_share.validate_unc_dir(root)
    return unreal_share.ShareState(parsed, ok, 0.0, '' if ok else 'unreachable')


def enable_unreal(monkeypatch, installs=(install(),), share='', share_state=None,
                  sampler=None, cache_reason='', lock_ok=True, symlink_eval=False,
                  force_gpu_index=None, recovered=True):
    """Enable Unreal with fakes; returns a dict of call records."""
    calls = {'prepare': 0, 'lock': 0, 'threads': 0, 'detect': 0}
    monkeypatch.setattr(config_unreal, 'ENABLED', True)
    monkeypatch.setattr(config_unreal, 'SHARE', share)
    monkeypatch.setattr(config_unreal.config, 'FORCE_GPU_INDEX', force_gpu_index)
    monkeypatch.setattr(runtime, '_is_windows', lambda: True)
    monkeypatch.setattr(runtime.symlink_policy, 'remote_symlink_evaluation_enabled', lambda: symlink_eval)

    def prepare(raw, env=None):
        calls['prepare'] += 1
        if cache_reason:
            raise cache_dir.CacheDirError(cache_reason)
        return cache_dir.PreparedCacheDir(path=Path('D:/uecache'), content_trusted=True)

    def lock(path):
        calls['lock'] += 1
        return lock_ok

    def start_threads():
        calls['threads'] += 1
        runtime._sampler = sampler

    def detect(**kwargs):
        calls['detect'] += 1
        return list(installs)

    monkeypatch.setattr(cache_dir, 'prepare_cache_dir', prepare)
    monkeypatch.setattr(cache_dir, 'complete_cache_dir', lambda prepared: calls.__setitem__('complete', 1))
    monkeypatch.setattr(runtime.owner_lock, 'acquire_for_process', lock)
    monkeypatch.setattr(runtime, '_start_threads', start_threads)
    monkeypatch.setattr(detection, 'detect_engines', detect)
    # Chunk 7b: the startup recovery would touch the (fake) cache dir; mark it done (or not) instead.
    monkeypatch.setattr(startup_recovery, 'run_once', lambda: None)
    if recovered:
        startup_recovery.mark_ready_for_tests()
    if share_state is not None:
        monkeypatch.setattr(unreal_share, 'state', lambda: share_state)
    return calls
