# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The per-claim job directory and its post-report cleanup (FR-PA-17, FR-UE-20; chunk 7 req. 3, 4)."""
import os
import threading
import time
from pathlib import Path

from sethlans_worker_agent.project_archive.layout import CacheLayout
from sethlans_worker_agent.unreal_executor import job_dir
from shared import owner_only_acl


def _layout(tmp_path) -> CacheLayout:
    root = Path(tmp_path) / 'uecache'
    owner_only_acl.create_owner_only_dir(root)
    layout = CacheLayout(root)
    layout.ensure_children()
    return layout


def test_create_copies_the_ue_scripts(tmp_path):
    job = job_dir.create(_layout(tmp_path), 42, '1a2b3c4d')
    assert job.root.name == '42-1a2b3c4d' and job.output.is_dir() and job.logs.is_dir()
    assert job.script(job_dir.PREP_SCRIPT).read_text(encoding='utf-8').count('search_all_assets(True)') == 1
    assert 'set_scalability' in job.script(job_dir.WARMUP_SCRIPT).read_text(encoding='utf-8')
    assert job.log('render', 2).name == 'render.2.log' and job.stdout('prep', 1).name == 'prep.1.stdout.txt'


def test_cleanup_removes_output_and_prunes_old_dirs_but_never_the_active_one(tmp_path):
    layout = _layout(tmp_path)
    old = []
    for index in range(4):
        job = job_dir.create(layout, index + 1, f'0000000{index}')
        stamp = time.time() - 1000 + index
        os.utime(job.root, (stamp, stamp))
        job_dir.release(job)  # those jobs finished long ago
        old.append(job)
    active = job_dir.create(layout, 99, 'aaaaaaaa')
    os.utime(active.root, (time.time() - 5000, time.time() - 5000))  # oldest by mtime, still kept
    (active.output / 'x.png').write_bytes(b'x')
    job_dir.cleanup_after_report(layout, active, keep_job_logs=3)
    names = sorted(p.name for p in layout.j.iterdir())
    assert names == sorted([active.root.name, old[3].root.name, old[2].root.name])
    assert not active.output.exists() and active.logs.exists()


def test_a_job_started_during_another_jobs_cleanup_is_never_pruned(tmp_path, monkeypatch):
    """Review M1/W1: A's cleanup runs after A released the machine, so B may already be creating its dir."""
    layout = _layout(tmp_path)
    a = job_dir.create(layout, 1, 'aaaaaaaa')
    os.utime(a.root, (time.time() + 100, time.time() + 100))  # A is the newest by mtime
    b_created, prune_started = threading.Event(), threading.Event()
    real_prune = job_dir.recovery.prune_job_dirs

    def slow_prune(*args, **kwargs):
        prune_started.set()
        assert b_created.wait(5)
        return real_prune(*args, **kwargs)
    monkeypatch.setattr(job_dir.recovery, 'prune_job_dirs', slow_prune)
    holder = {}

    def start_b():
        assert prune_started.wait(5)
        holder['b'] = job_dir.create(layout, 2, 'bbbbbbbb')
        os.utime(holder['b'].root, (time.time() - 5000, time.time() - 5000))  # oldest: first in line to prune
        b_created.set()
    thread = threading.Thread(target=start_b)
    thread.start()
    job_dir.cleanup_after_report(layout, a, keep_job_logs=1)
    thread.join(10)
    assert holder['b'].root.exists() and a.root.exists()
    job_dir.cleanup_after_report(layout, holder['b'], keep_job_logs=1)
    assert len(list(layout.j.iterdir())) == 1  # B finished: only the newest dir stays
    assert job_dir.active_names() == frozenset()


def test_cleanup_tolerates_entries_that_vanish(tmp_path, monkeypatch):
    layout = _layout(tmp_path)
    job = job_dir.create(layout, 1, 'aaaaaaaa')
    gone = job_dir.create(layout, 2, 'bbbbbbbb')
    job_dir.release(gone)
    real_lstat = job_dir.fs_safe.lstat

    def lstat(path):
        if Path(path).name == gone.root.name:
            raise FileNotFoundError(path)
        return real_lstat(path)
    monkeypatch.setattr(job_dir.fs_safe, 'lstat', lstat)
    job_dir.cleanup_after_report(layout, job, keep_job_logs=0)
    assert not job.root.exists()  # keep 0: this job's own dir goes too; the vanished entry is skipped
