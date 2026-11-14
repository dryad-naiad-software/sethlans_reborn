# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Single owner per Unreal cache dir (Unreal foundation FR-WC-6, AC-28;
FR-T-6 "Owner lock"). Real files and a second OS process.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from sethlans_worker_agent.project_archive import owner_lock

REPO_ROOT = Path(__file__).resolve().parents[3]

CHILD = textwrap.dedent("""
    import sys
    from sethlans_worker_agent.project_archive import owner_lock
    lock = owner_lock.OwnerLock(sys.argv[1])
    print('acquired' if lock.try_acquire() else 'busy', flush=True)
""")


def _child_env():
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join([str(REPO_ROOT / 'worker'), str(REPO_ROOT)])
    return env


def _try_in_other_process(cache_dir: Path) -> str:
    result = subprocess.run([sys.executable, '-c', CHILD, str(cache_dir)], capture_output=True,
                            text=True, timeout=60, env=_child_env(), cwd=str(REPO_ROOT))
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture(autouse=True)
def _release():
    owner_lock.release_for_process()
    yield
    owner_lock.release_for_process()


def test_second_process_is_refused_while_held(tmp_path):
    assert owner_lock.acquire_for_process(tmp_path) is True
    assert (tmp_path / owner_lock.LOCK_FILENAME).exists()
    assert _try_in_other_process(tmp_path) == 'busy'
    owner_lock.release_for_process()
    assert _try_in_other_process(tmp_path) == 'acquired'


def test_lock_held_by_another_process_disables_unreal(tmp_path, monkeypatch):
    """The second worker reports the reason and never gets past the lock (no recovery)."""
    holder = subprocess.Popen(
        [sys.executable, '-c', CHILD + "import time; time.sleep(60)\n", str(tmp_path)],
        stdout=subprocess.PIPE, text=True, env=_child_env(), cwd=str(REPO_ROOT))
    try:
        assert holder.stdout.readline().strip() == 'acquired'
        from sethlans_worker_agent.unreal_executor import capabilities, runtime
        from tests._unreal_worker_state import enable_unreal
        runtime.reset_for_tests()
        real_acquire = owner_lock.acquire_for_process
        calls = enable_unreal(monkeypatch)
        monkeypatch.setattr(owner_lock, 'acquire_for_process', real_acquire)
        monkeypatch.setattr(runtime.cache_dir, 'prepare_cache_dir',
                            lambda raw, env=None: runtime.cache_dir.PreparedCacheDir(tmp_path, True))
        snap = capabilities.snapshot()
        assert snap['enabled'] is False
        assert snap['disabled_reason'] == owner_lock.OWNER_LOCK_REASON
        assert calls['threads'] == 0
    finally:
        holder.kill()
        holder.wait(timeout=30)
        holder.stdout.close()
        from sethlans_worker_agent.unreal_executor import runtime
        runtime.reset_for_tests()


def test_in_process_second_handle_is_refused(tmp_path):
    first, second = owner_lock.OwnerLock(tmp_path), owner_lock.OwnerLock(tmp_path)
    assert first.try_acquire() is True
    try:
        assert second.try_acquire() is False
        assert first.try_acquire() is True  # idempotent for the holder
    finally:
        first.release()
        first.release()  # idempotent
    assert second.try_acquire() is True
    second.release()
