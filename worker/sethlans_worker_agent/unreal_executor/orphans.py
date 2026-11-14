# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Orphan sweep (Unreal foundation FR-UE-23; Conc H1, Sec R3-W1; review S2, S6, L5).

``sweep(cache_dir, engine_roots)`` runs as FR-PA-18 step 1, before any
deletion. It kills:

1. every process listed in a ``j/*/procs.json`` ledger that is alive, whose
   ``(create_time, exe)`` match the entry (guards PID reuse), whose owner is
   the worker's account and whose image lies under a detected engine root or
   ``CACHE_DIR`` (an entry without an image is skipped); a listed zenserver
   only if it is this worker's farm zenserver;
2. every ``UnrealEditor-Cmd.exe``, ``UnrealEditor.exe``,
   ``ShaderCompileWorker.exe`` or ``CrashReportClient*.exe`` whose image lies
   under a detected engine root (exact, case-insensitive path prefix) **and**
   whose command line names ``CACHE_DIR`` **as a path**: the normalized
   cache path preceded by the start, whitespace, a quote or ``=`` and
   followed by a separator, a quote, whitespace or the end. ``D:\\ue`` does
   not match ``D:\\UE_Projects\\...``, nor ``D:\\uecache`` ``D:\\uecache2``.
   The working directory is no signal (UE resets it to
   ``Engine\\Binaries\\Win64``) and is not used;
3. a farm zenserver (``zen_private.is_farm_zenserver``: image, owner SID and
   an **equal** ``--data-dir``), also when no ledger lists it.

Any other zenserver is never swept, and the name rule is not widened to
``crashpad_handler``/``UnrealTraceServer`` (other applications use those
images; the ledger covers the job's own). ``AccessDenied`` and
``NoSuchProcess`` are tolerated. UE editor roots are killed first (so they
cannot relaunch workers), then the rest; the processes are re-enumerated
once afterwards. ``SweepResult.survivors`` lists what did not die.

**Contract for chunk 7b:** recovery calls its ``sweep`` hook with no
arguments and ignores the return value, so pass a closure::

    def _sweep():
        result = orphans.sweep(cache_dir, engine_roots, worker_sid)
        if result.survivors:
            runtime.disable_until_restart("orphaned Unreal processes")

    recovery.recover(prepared, limits, keep_job_logs=..., sweep=_sweep)

Called only that way (FR-PA-18 step 1). If the closure raises, recovery
stops with its sweep disable reason and deletes nothing.
"""
import json
import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Pattern, Tuple

import psutil

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor import zen_private

logger = logging.getLogger(__name__)

LEDGER_NAME = 'procs.json'
MAX_LEDGER_BYTES = 4 * 1024 * 1024
UE_IMAGES = frozenset({'unrealeditor-cmd.exe', 'unrealeditor.exe', 'shadercompileworker.exe'})
UE_ROOT_IMAGES = frozenset({'unrealeditor-cmd.exe', 'unrealeditor.exe'})
ZEN_IMAGES = (zen_private.ZEN_IMAGE_WINDOWS, zen_private.ZEN_IMAGE_POSIX)
CRASH_REPORTER_PREFIX = 'crashreportclient'
CREATE_TIME_TOLERANCE = 0.01
KILL_WAIT_SECONDS = 10.0
Survivors = Tuple[Tuple[int, str], ...]


@dataclass(frozen=True)
class SweepResult:
    killed: Survivors = ()
    errors: int = 0
    survivors: Survivors = ()


def _read_ledger(ledger: Path) -> List[dict]:
    st = fs_safe.lstat(ledger)
    if fs_safe.is_reparse(st) or not stat.S_ISREG(st.st_mode):
        return []
    with open(fs_safe.long_path(ledger), 'rb') as handle:
        data = handle.read(MAX_LEDGER_BYTES + 1)
    if len(data) > MAX_LEDGER_BYTES:
        return []
    items = json.loads(data.decode('utf-8')).get('procs', [])
    return [item for item in items if isinstance(item, dict)]


def read_ledgers(cache_dir) -> List[dict]:
    """Entries of every ``j/*/procs.json`` (links are not followed; bad files are skipped)."""
    entries: List[dict] = []
    jobs = Path(cache_dir) / 'j'
    try:
        if fs_safe.is_reparse(fs_safe.lstat(jobs)):
            return entries
        names = sorted(os.listdir(fs_safe.long_path(jobs)))
    except OSError:
        return entries
    for name in names:
        try:
            if not fs_safe.is_reparse(fs_safe.lstat(jobs / name)):
                entries.extend(_read_ledger(jobs / name / LEDGER_NAME))
        except (OSError, ValueError, AttributeError):
            continue
    return entries


def _under(path: str, roots: Iterable[str]) -> bool:
    folded = os.path.normcase(path)
    return any(folded.startswith((root.rstrip('\\/') + '\\', root.rstrip('\\/') + '/')) for root in roots)


def cache_reference_pattern(cache_dir) -> Pattern:
    """``CACHE_DIR`` as a whole path inside a normcase-d command line (Sec S2)."""
    spellings = {os.path.normcase(str(cache_dir).rstrip('\\/')), zen_private.normalize_path(str(cache_dir))}
    alternatives = '|'.join(re.escape(text) for text in sorted(spellings) if text)
    return re.compile(r'(?:^|[\s"\'=])(?:' + alternatives + r')(?=[\\/"\'\s]|$)')


def _references(proc, pattern: Pattern) -> bool:
    try:
        return pattern.search(os.path.normcase(' '.join(proc.cmdline()))) is not None
    except (psutil.Error, OSError):
        return False


def _name(proc) -> str:
    try:
        return proc.name().lower()
    except (psutil.Error, OSError):
        return ''


def _is_ue_image(name: str) -> bool:
    return name in UE_IMAGES or (name.startswith(CRASH_REPORTER_PREFIX) and name.endswith('.exe'))


def _ledger_match(entry: dict, process_factory: Callable, trusted_roots: List[str], worker_sid: Optional[str]):
    try:
        exe = str(entry.get('exe') or '')
        if not exe or not _under(exe, trusted_roots):
            return None
        proc = process_factory(int(entry['pid']))
        if abs(proc.create_time() - float(entry['create_time'])) > CREATE_TIME_TOLERANCE:
            return None
        if os.path.normcase(proc.exe() or '') != os.path.normcase(exe):
            return None
        return proc if zen_private.owned_by_worker(proc, worker_sid) else None
    except (psutil.Error, OSError, KeyError, TypeError, ValueError):
        return None


class _Sweep:

    def __init__(self, cache_dir, engine_roots, worker_sid: Optional[str]):
        self.cache_dir = cache_dir
        self.pattern = cache_reference_pattern(cache_dir)
        self.roots = [os.path.normcase(str(root)) for root in engine_roots]
        self.trusted = self.roots + [os.path.normcase(str(cache_dir))]
        self.worker_sid = worker_sid
        self.targets: Dict[int, object] = {}
        self.errors = 0

    def consider_ledger(self, proc) -> None:
        name = _name(proc)
        if not name:
            return
        if name in ZEN_IMAGES and not zen_private.is_farm_zenserver(proc, self.cache_dir, self.worker_sid):
            return
        self.targets[proc.pid] = proc

    def consider_running(self, proc) -> None:
        name = _name(proc)
        if name in ZEN_IMAGES:
            if zen_private.is_farm_zenserver(proc, self.cache_dir, self.worker_sid):
                self.targets[proc.pid] = proc
            return
        if not _is_ue_image(name):
            return
        try:
            exe = proc.exe() or ''
        except (psutil.Error, OSError):
            return
        if _under(exe, self.roots) and _references(proc, self.pattern):
            self.targets[proc.pid] = proc

    def kill(self, targets: Dict[int, object]) -> Tuple[List[Tuple[int, str]], List]:
        procs = sorted(targets.values(), key=lambda p: (0 if _name(p) in UE_ROOT_IMAGES else 1, p.pid))
        killed = []
        for proc in procs:
            try:
                exe = proc.exe() or proc.name()
                proc.kill()
                killed.append((proc.pid, exe))
            except psutil.NoSuchProcess:
                continue
            except (psutil.Error, OSError):
                self.errors += 1
        _gone, alive = psutil.wait_procs(procs, timeout=KILL_WAIT_SECONDS) if procs else ([], [])
        return killed, list(alive)


def _running(process_iter: Callable, state: _Sweep) -> List:
    try:
        return [proc for proc in process_iter() if proc.pid != os.getpid()]
    except (psutil.Error, OSError):
        state.errors += 1
        return []


def _describe(procs: Iterable) -> Survivors:
    out = []
    for proc in procs:
        try:
            out.append((proc.pid, proc.exe() or proc.name()))
        except (psutil.Error, OSError):
            out.append((proc.pid, '?'))
    return tuple(sorted(out))


def sweep(cache_dir, engine_roots: Iterable, worker_sid: Optional[str] = None,
          process_iter: Callable = psutil.process_iter, process_factory: Callable = psutil.Process) -> SweepResult:
    """FR-UE-23: kill orphaned farm UE processes. Never raises."""
    state = _Sweep(cache_dir, list(engine_roots), worker_sid)
    for entry in read_ledgers(cache_dir):
        proc = _ledger_match(entry, process_factory, state.trusted, worker_sid)
        if proc is not None and proc.pid != os.getpid():
            state.consider_ledger(proc)
    for proc in _running(process_iter, state):
        state.consider_running(proc)
    first, state.targets = state.targets, {}
    killed, alive = state.kill(first)
    for proc in _running(process_iter, state):  # one re-enumeration: anything relaunched meanwhile
        if proc.pid not in first:
            state.consider_running(proc)
    if state.targets:
        late_killed, late_alive = state.kill(state.targets)
        killed, alive = killed + late_killed, alive + late_alive
    result = SweepResult(killed=tuple(killed), errors=state.errors, survivors=_describe(alive))
    if result.killed:
        logger.warning("Orphan sweep killed %d Unreal process(es): %s", len(result.killed), result.killed)
    if result.survivors:
        logger.error("Orphan sweep could not kill: %s", result.survivors)
    return result
