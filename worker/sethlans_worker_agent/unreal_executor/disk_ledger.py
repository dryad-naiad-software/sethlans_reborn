# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The disk check of sequence pieces with several Unreal jobs at once (render per card D12,
FR-PC-DSK-1; extends sequence rendering FR-SW-11). Keyed by ``claim_id`` (D16).

* ``register(claim_id)`` in ``try_reserve`` (need 0, no folder yet) for **every** Unreal claim;
  ``attach(claim_id, job_dir)`` once the claim's ``j\\<id>-<att8>\\`` exists (``job_run``, before
  any launch); ``unregister(claim_id)`` from the reservation's release and from
  ``retain_until_restart``.
* ``check_and_register(claim_id, job_dir, need, output_dir, reserve_gb)`` (a piece, before its
  prep): outside the lock it walks every other registered claim's ``output\\`` (``written_i``) and
  the job folders of **no** running claim (``leftover``: a finished job's frames wait for its
  post-report cleanup); under the lock it re-reads the registry and
  ``shutil.disk_usage(output_dir).free`` and admits when::

      need <= free + leftover - reserve - sum(max(0, need_i - written_i))

  ``free`` is the smaller of the free space read before the walk and the one read under the lock: a
  finished job's frames deleted after the walk counted them as leftover would otherwise count twice.
  A claim registered (or attached) after the walk counts ``written_i = 0`` and its folder is not
  leftover. Admitted, a registered claim keeps ``need`` as its estimate (an unregistered one, already
  released, is never added back); refused, ``LocalConflict`` (the piece is released), and its estimate
  stays as it was.

The output folders of running pilots, ``audio`` and ``shots`` claims (need 0) are therefore
never counted as free. A Blender job writes elsewhere and is not counted (an accepted gap).

``_lock`` is a leaf lock (NF-1): only ``disk_usage`` runs under it, no other lock is taken.
"""
import os
import shutil
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, Optional

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor.outcomes import LocalConflict

GIB = 1024 ** 3
DISK_MESSAGE = ("This piece needs up to {need:.1f} GB for its {frames} frames, but the cache drive has only "
                "{free:.1f} GB free above the {reserve} GB reserve.")


@dataclass(frozen=True)
class Entry:
    need: int = 0
    job_dir: Optional[Path] = None


def output_bytes(job_dir) -> int:
    """Bytes under ``job_dir\\output`` (no link followed); 0 when it is missing, unsafe or gone meanwhile."""
    out = Path(job_dir) / 'output'
    try:
        if not out.is_dir():
            return 0
        return sum(fs_safe.lstat(out / rel).st_size for rel in fs_safe.walk_tree(out).files)
    except OSError:
        return 0


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


class DiskLedger:
    """One per worker process (``ledger``); fresh ones in tests."""

    def __init__(self, free_bytes: Callable[[object], int] = lambda path: shutil.disk_usage(path).free,
                 walk: Callable[[object], int] = output_bytes):
        self._free, self._walk = free_bytes, walk
        self._lock = threading.Lock()
        self._claims: Dict[str, Entry] = {}

    def register(self, claim_id: str) -> None:
        with self._lock:
            if claim_id in self._claims:
                raise ValueError(f"claim {claim_id} is already in the disk ledger")
            self._claims[claim_id] = Entry()

    def attach(self, claim_id: str, job_dir) -> bool:
        """The claim's job folder now exists; False when the claim is not registered."""
        with self._lock:
            entry = self._claims.get(claim_id)
            if entry is not None:
                self._claims[claim_id] = replace(entry, job_dir=Path(job_dir))
            return entry is not None

    def unregister(self, claim_id: str) -> None:
        with self._lock:
            self._claims.pop(claim_id, None)

    def snapshot(self) -> Dict[str, Entry]:
        with self._lock:
            return dict(self._claims)

    def _walk_outside_lock(self, claim_id: str, job_dir: Optional[Path]):
        """``(written per other claim, leftover bytes per folder of no claim)``, read without the lock.

        Without ``job_dir`` (no job folder: test flows) there is no jobs folder to look for leftovers in.
        """
        claims = {cid: e for cid, e in self.snapshot().items() if cid != claim_id and e.job_dir is not None}
        written = {cid: (_key(e.job_dir), self._walk(e.job_dir)) for cid, e in claims.items() if e.need > 0}
        if job_dir is None:
            return written, {}
        owned = {_key(e.job_dir) for e in claims.values()} | {_key(job_dir)}
        leftover = {}
        try:
            folders = list(job_dir.parent.iterdir())
        except OSError:
            folders = []
        for folder in folders:
            if _key(folder) not in owned:
                leftover[_key(folder)] = self._walk(folder)
        return written, leftover

    @staticmethod
    def _pending(others: Dict[str, Entry], written: dict) -> int:
        """Bytes the other claims may still write; a claim not walked (registered later) has written 0."""
        pending = 0
        for cid, entry in others.items():
            seen = written.get(cid)
            done = seen[1] if seen is not None and entry.job_dir is not None and seen[0] == _key(entry.job_dir) else 0
            pending += max(0, entry.need - done)
        return pending

    def check_and_register(self, claim_id: str, job_dir, need: int, output_dir, reserve_gb: int, *,
                           frames: int = 0) -> int:
        """Admit the piece's ``need`` bytes or raise ``LocalConflict``; returns ``need``.

        ``frames`` (the piece's rendered frame count) only goes into the refusal message.
        """
        job_dir = Path(job_dir) if job_dir is not None else None
        free_before = self._free(output_dir)
        written, leftover = self._walk_outside_lock(claim_id, job_dir)
        with self._lock:
            others = {cid: e for cid, e in self._claims.items() if cid != claim_id}
            owned_now = {_key(e.job_dir) for e in others.values() if e.job_dir is not None}
            free_leftover = sum(size for folder, size in leftover.items() if folder not in owned_now)
            free = min(free_before, self._free(output_dir))
            available = free + free_leftover - reserve_gb * GIB - self._pending(others, written)
            if need > available:
                raise LocalConflict('local_conflict', DISK_MESSAGE.format(
                    need=need / GIB, frames=frames, free=max(available, 0) / GIB, reserve=reserve_gb))
            if claim_id in self._claims:
                self._claims[claim_id] = Entry(need=need, job_dir=job_dir)
        return need


@dataclass(frozen=True)
class DiskTarget:
    """A claim's handle on the ledger (``UnrealReservation.disk_target()``), used on the job thread."""
    ledger: DiskLedger
    claim_id: str

    def attach(self, job_dir) -> bool:
        return self.ledger.attach(self.claim_id, job_dir)

    def check(self, job_dir, need: int, output_dir, reserve_gb: int, frames: int = 0) -> int:
        return self.ledger.check_and_register(self.claim_id, job_dir, need, output_dir, reserve_gb, frames=frames)


ledger = DiskLedger()


def reset_for_tests() -> None:
    """Empty the worker's ledger (test isolation only)."""
    with ledger._lock:
        ledger._claims.clear()
