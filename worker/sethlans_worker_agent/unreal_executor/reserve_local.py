# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The RAM gate, reserver check 9a (render per card D9, FR-PC-RAM-4, FR-PC-RAM-6), and its Blender side.

``ram_gate(params, gate, slot, deps, ledger)`` runs in ``Reserver.try_reserve`` on the agent-loop
thread, after the fresh VRAM gate and the card choice. It always works out the job's need
(``job_ram.need``) so the reserver can register it in the RAM ledger, and it **checks** the need
only when another job runs on the machine: the capacity holds a reservation, or the RAM ledger
holds a claim (Unreal or Blender). A job alone is never held (D9: today's behaviour, no starvation).

* **Warm-up planned** (FR-PC-RAM-4): the same test the job's flow makes (``warmup.decide`` on the
  slot's marker with the warm-up CVar hash): a pilot always warms up; a normal job when its pair is
  absent **or** the marker is stale (other farm defaults or shader-relevant CVars). Without the
  archive's cache entry (``ReserveDeps.entry_dir`` gives ``None``) the marker is absent, so a
  warm-up is planned. A ``shots`` job runs the prep only: no warm-up.
* **Measured figures** (per-machine memory test D11, FR-MT-G-3): the ``ram`` of the result the gate
  chose for the job's card (``gate.result``, the same record and card kind as the gate's peak); a pilot,
  a ``shots`` job or a result without RAM figures uses the defaults (``job_ram.need``).
* **Card size** for the commit default: the gated card's ``total_mb`` (``gate.summary['vram_mb']``);
  ``shots`` 0 (its prep commandlet uses no GPU, D3). A gated job without the figure is declined.
* **Physical:** ``need_ws <= available - ram_reserve - outstanding_ws``, ``available`` =
  ``psutil.virtual_memory().available`` (psutil 7.0.0, checked on the worker's venv 2026-11-21).
* **Commit:** ``need_commit <= headroom - commit_reserve - outstanding_commit``, ``headroom`` =
  ``process_memory.commit_headroom()`` (the current ``CommitLimit``: page-file growth never counts).
* ``outstanding`` is ``ram_ledger.outstanding``: the other claims' ``need - current`` and
  ``ram_blender_mb`` per Blender job claimed in the last 300 s. A failed or stale figure there, or
  a reader that fails, declines with ``ram_ledger.UNAVAILABLE_REASON`` (fail closed, NF-2).

``blender_fits(ledger)`` is D9's Blender side (``unreal_bridge.blender_ram_block``, before a Blender GPU
reservation while an Unreal job runs): ``headroom - reserve - outstanding - ram_blender_mb >= 0`` for both
kinds (``ram_blender_mb`` is the Blender job's own memory).
Sizes are MB (bytes // 1,048,576).
"""
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from sethlans_worker_agent.unreal_executor import cvars, job_ram, warmup
from sethlans_worker_agent.unreal_executor.ram_ledger import UNAVAILABLE_REASON

logger = logging.getLogger(__name__)

MB = 1024 * 1024
PHYSICAL_MESSAGE = "needs {need} MB of physical RAM; {free} MB free above the {reserve} MB reserve while other jobs run."
COMMIT_MESSAGE = "needs {need} MB of commit; {free} MB commit headroom above the {reserve} MB reserve."
BLENDER_PHYSICAL = ("needs {own} MB of physical RAM beside {out} MB the running jobs may still take; {free} MB free "
                    "above the {reserve} MB reserve.")
BLENDER_COMMIT = ("needs {own} MB of commit beside {out} MB the running jobs may still take; {free} MB commit headroom "
                  "above the {reserve} MB reserve.")
NO_CARD_SIZE = "the size of its graphics card is not known, so its RAM need cannot be worked out."


def available_mb() -> int:
    """``psutil.virtual_memory().available`` in MB."""
    import psutil
    return int(psutil.virtual_memory().available) // MB


def commit_headroom_mb() -> int:
    """``(CommitLimit - CommitTotal) * PageSize`` in MB (Windows; raises elsewhere)."""
    from sethlans_worker_agent.unreal_executor import process_memory
    return process_memory.commit_headroom() // MB


@dataclass(frozen=True)
class RamReaders:
    available_mb: Callable[[], int]
    commit_headroom_mb: Callable[[], int]


def default_readers() -> RamReaders:
    """The real readers, looked up at call time (``ReserveDeps.ram_readers`` ``None``)."""
    return RamReaders(available_mb, commit_headroom_mb)


@dataclass(frozen=True)
class RamSettings:
    ram_reserve_mb: int
    commit_reserve_mb: int
    default_ws_mb: int
    default_commit_mb: int
    blender_mb: int

    @classmethod
    def from_config(cls) -> 'RamSettings':
        from sethlans_worker_agent import config_unreal as c
        return cls(c.RAM_RESERVE_MB, c.COMMIT_RESERVE_MB, c.RAM_DEFAULT_WS_MB, c.RAM_DEFAULT_COMMIT_MB,
                   c.RAM_BLENDER_MB)


@dataclass(frozen=True)
class RamDecision:
    ok: bool
    need: Optional[job_ram.RamNeed] = None
    message: str = ''
    checked: bool = False          # the checks ran (another job runs)


def warmup_planned(params, entry_dir, slot: int) -> bool:
    """FR-PC-RAM-4 with the reviewer's note: absent or stale marker, exactly as ``warmup.decide``."""
    if params.is_shots:
        return False
    if entry_dir is None:
        return True  # no cache entry: no marker in any slot
    marker = warmup.marker_path(entry_dir, params.overlay_sha256, params.engine_build, slot)
    return warmup.decide(marker, params, cvars.warmup_cvars_sha256(params.merged)) is not None


def card_total_mb(params, gate) -> int:
    if params.is_shots:
        return 0  # D3: the prep commandlet uses no GPU memory
    total = (getattr(gate, 'summary', None) or {}).get('vram_mb')
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError(NO_CARD_SIZE)
    return total


def measured_ram(gate) -> Optional[job_ram.MeasuredRam]:
    """D11: the RAM figures of the result the gate chose (``None``: no result, or one without figures)."""
    result = getattr(gate, 'result', None)
    return result.ram if result is not None else None


def others_running(capacity, ledger) -> bool:
    """Another job runs: the capacity holds a reservation or the RAM ledger a claim."""
    return (capacity is not None and not capacity.has_no_reservations()) or ledger.has_claims()


def _read(reader: Callable[[], int], what: str) -> Optional[int]:
    try:
        return int(reader())
    except Exception as exc:  # noqa: BLE001 - any failed read fails the gate closed (NF-2)
        logger.warning("The RAM gate could not read %s (%s: %s); waiting.", what, type(exc).__name__, exc)
        return None


def check(need: job_ram.RamNeed, ledger, readers: RamReaders, settings: RamSettings) -> RamDecision:
    """D9's two checks of ``need`` beside the running jobs."""
    out = ledger.outstanding(settings.blender_mb)
    if out.reason:
        return RamDecision(False, need, out.reason, True)
    available = _read(readers.available_mb, 'the free physical RAM')
    if available is None:
        return RamDecision(False, need, UNAVAILABLE_REASON, True)
    free = available - settings.ram_reserve_mb - out.ws
    if need.ws_mb > free:
        return RamDecision(False, need, PHYSICAL_MESSAGE.format(need=need.ws_mb, free=max(free, 0),
                                                                reserve=settings.ram_reserve_mb), True)
    headroom = _read(readers.commit_headroom_mb, 'the commit headroom')
    if headroom is None:
        return RamDecision(False, need, UNAVAILABLE_REASON, True)
    free = headroom - settings.commit_reserve_mb - out.commit
    if need.commit_mb > free:
        return RamDecision(False, need, COMMIT_MESSAGE.format(need=need.commit_mb, free=max(free, 0),
                                                              reserve=settings.commit_reserve_mb), True)
    return RamDecision(True, need, '', True)


def ram_gate(params, gate, slot: int, deps, ledger) -> RamDecision:
    """Check 9a: the job's need, checked only beside another job (``deps`` is the ``ReserveDeps``)."""
    settings = RamSettings.from_config()
    entry_dir = deps.entry_dir(params.archive_sha256) if deps.entry_dir is not None else None
    try:
        total = card_total_mb(params, gate)
    except ValueError as exc:
        return RamDecision(False, None, str(exc))
    need = job_ram.need(measured_ram(gate), warmup_planned(params, entry_dir, slot), total,
                        settings.default_ws_mb, settings.default_commit_mb)
    capacity = deps.capacity() if deps.capacity is not None else None
    if not others_running(capacity, ledger):
        return RamDecision(True, need)
    return check(need, ledger, deps.ram_readers or default_readers(), settings)


def blender_fits(ledger, readers: Optional[RamReaders] = None, settings: Optional[RamSettings] = None) -> str:
    """D9's Blender side: ``''`` when it fits, else why not (fail closed)."""
    readers, settings = readers or default_readers(), settings or RamSettings.from_config()
    out = ledger.outstanding(settings.blender_mb)
    if out.reason:
        return out.reason
    available = _read(readers.available_mb, 'the free physical RAM')
    if available is None:
        return UNAVAILABLE_REASON
    own = settings.blender_mb
    free = available - settings.ram_reserve_mb
    if free - out.ws - own < 0:
        return BLENDER_PHYSICAL.format(own=own, out=out.ws, free=max(free, 0), reserve=settings.ram_reserve_mb)
    headroom = _read(readers.commit_headroom_mb, 'the commit headroom')
    if headroom is None:
        return UNAVAILABLE_REASON
    free = headroom - settings.commit_reserve_mb
    if free - out.commit - own < 0:
        return BLENDER_COMMIT.format(own=own, out=out.commit, free=max(free, 0), reserve=settings.commit_reserve_mb)
    return ''
