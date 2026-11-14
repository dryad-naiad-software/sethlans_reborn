# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The per-claim job directory ``CACHE_DIR\\j\\<job_id>-<att8>\\`` (Unreal
foundation FR-PA-1, FR-PA-17, FR-UE-8, FR-UE-20)::

    params.json  warmup_params.json  prep_result.json  warmup_result.json
    procs.json   logs\\  scripts\\  output\\

Every directory is created owner-only (or checked: owner, no link). The
worker-bundled UE scripts (``ue_scripts/``, PyInstaller data) are copied
into ``scripts\\`` and run from there, so their paths are under
``CACHE_DIR`` and contain no space, ``,`` or ``;`` (FR-WC-3).
``cleanup_after_report`` removes ``output\\`` (FR-PA-17) and prunes old
job directories, never the active one (chunk 7 req. 3, 4).
"""
import logging
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Set, Tuple

from sethlans_worker_agent.project_archive import fs_safe, recovery
from sethlans_worker_agent.project_archive.layout import CacheLayout

logger = logging.getLogger(__name__)

SCRIPTS_SOURCE = Path(__file__).resolve().parent / 'ue_scripts'
PREP_SCRIPT = 'sethlans_prepare_queue.py'
WARMUP_SCRIPT = 'sethlans_warm_up.py'

_active_lock = threading.Lock()  # leaf lock
_active: Set[str] = set()        # names of j/ dirs a job still uses (never pruned)


@dataclass(frozen=True)
class JobDir:
    root: Path

    @property
    def logs(self) -> Path:
        return self.root / 'logs'

    @property
    def scripts(self) -> Path:
        return self.root / 'scripts'

    @property
    def output(self) -> Path:
        return self.root / 'output'

    @property
    def params(self) -> Path:
        return self.root / 'params.json'

    @property
    def warmup_params(self) -> Path:
        return self.root / 'warmup_params.json'

    @property
    def prep_result(self) -> Path:
        return self.root / 'prep_result.json'

    @property
    def warmup_result(self) -> Path:
        return self.root / 'warmup_result.json'

    @property
    def ledger(self) -> Path:
        return self.root / 'procs.json'

    def log(self, phase: str, attempt: int) -> Path:
        return self.logs / f"{phase}.{int(attempt)}.log"

    def stdout(self, phase: str, attempt: int) -> Path:
        return self.logs / f"{phase}.{int(attempt)}.stdout.txt"

    def script(self, name: str) -> Path:
        return self.scripts / name


def create(layout: CacheLayout, job_id: int, att8: str) -> JobDir:
    """Create ``j/<id>-<att8>/`` and its children owner-only; copy the UE scripts.

    The name is registered as **active** before anything is created, so a
    concurrent cleanup of another job never prunes it (review M1/W1).
    """
    layout.check_child('j')
    job = JobDir(layout.job_dir(job_id, att8))
    with _active_lock:
        _active.add(job.root.name)
    for path in (job.root, job.logs, job.scripts, job.output):
        fs_safe.ensure_owned_dir(path)
    for name in (PREP_SCRIPT, WARMUP_SCRIPT):
        shutil.copyfile(SCRIPTS_SOURCE / name, job.script(name))
    return job


def release(job: JobDir) -> None:
    """The job no longer needs its directory protected from pruning."""
    with _active_lock:
        _active.discard(job.root.name)


def active_names() -> frozenset:
    with _active_lock:
        return frozenset(_active)


def reset_for_tests() -> None:
    with _active_lock:
        _active.clear()


def _entries(layout: CacheLayout, active: frozenset) -> List[Tuple[int, str]]:
    entries = []
    for child in layout.j.iterdir():
        if child.name in active:
            continue
        try:
            entries.append((fs_safe.lstat(child).st_mtime_ns, child.name))
        except FileNotFoundError:
            continue  # removed meanwhile
    return entries


def cleanup_after_report(layout: CacheLayout, job: JobDir, keep_job_logs: int) -> None:
    """FR-PA-17 output removal, then pruning to the newest ``keep_job_logs`` dirs. Never raises.

    This job stops being active first; every directory still active (a job that
    started after this one released its resources) is excluded from pruning.
    """
    release(job)
    try:
        fs_safe.remove_tree(job.output)
        recovery.prune_job_dirs(layout, max(keep_job_logs, 0), _entries(layout, active_names()))
    except OSError as exc:
        logger.warning("Unreal job directory cleanup failed: %s", exc)
