# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Spill-over into shared memory, Windows (Unreal per-machine memory test D9,
FR-MT-S-2).

Every render launch of a job or a pilot gets a ``SpillWatch``
(``watch_for(gate, job_id)``, set on the phase runner by ``job_run``; the
phase runner gives one to each render launch, ``RunSpec.spill``). The
launch's ``VramTrace`` feeds it every attributed sample of the **whole**
launch, start-up spike included, on the runner thread: the job's own shared
usage on its card (its Job Object members in the reading's
``per_pid_shared``), their dedicated usage on the card (``per_pid``) and the
card's ``used``.

* **Over** sample: own shared > ``reference + margin``; ``reference`` is the
  gate's result ``shared_peak_mb`` (the pilot's own figure at the same size,
  D9), ``margin`` is ``unreal.spill_margin_mb`` (read when the watch is made).
* **Spilled** after ``OVER_SAMPLES`` (2) consecutive over samples; a sample
  without the job's shared figure on its card (no ``per_pid_shared``, or the
  card missing from it), without per-pid attribution (no Job Object), or no
  reading at all (none in time, or one without the job's card) breaks the
  run; a repeated reading is no new sample (``VramTrace.add``).
* **Cause**, at each over sample: ``others = card used - own dedicated``;
  ``others > used_at_gate + margin`` (``used_at_gate``: the card's ``used`` in
  the fresh gate's reading, ``GateResult.used_mb``) makes it ``contention``.
  Mixed causes count as contention: a spill is ``contention`` when **any**
  over sample of a run that reached ``OVER_SAMPLES`` was, else ``self``.
  After another program's squeeze the job's memory can stay in shared memory
  for a while, and calling those later samples ``self`` would mark the result
  does_not_fit for good, while a real self-overflow repeats on later jobs of
  the key and is reported again.
* ``spilled`` is ``None`` without a reference (a pilot, or a result whose
  ``shared_peak_mb`` is null), without ``used_at_gate``, or when no sample
  carried the job's shared figure (no reading, no ``per_pid_shared``: not
  Windows, a Windows build without the counter, no Job Object); then
  ``spill_cause`` is ``None`` too (NF-2: nothing is marked without evidence).

The job always runs on: the watch only records (one WARNING when a launch
first spills). ``outcome(runs)`` combines the render launches of a job (a
render retry is a second launch): spilled when any launch spilled,
``contention`` when any spilled launch's cause was, the largest figures.
``summary_values(runs)`` gives the FR-MT-S-2 summary keys.
"""
import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from sethlans_worker_agent import config_unreal

logger = logging.getLogger(__name__)

SELF, CONTENTION = 'self', 'contention'
OVER_SAMPLES = 2


@dataclass
class SpillWatch:
    """D9 over one render launch, fed sample by sample by the launch's ``VramTrace``."""
    reference_mb: Optional[int]     # the gate's result shared_peak_mb; None: a pilot, or no reference
    used_at_gate_mb: Optional[int]  # the card's used in the fresh gate's reading
    margin_mb: int
    job_id: object = None           # for the log line only
    samples: int = 0                # samples that carried the job's own shared figure on its card
    shared_peak_mb: Optional[int] = None
    others_peak_mb: Optional[int] = None
    run: int = 0                    # consecutive over samples so far
    run_contention: bool = False    # an over sample of the current run was contention
    spilled_seen: bool = False
    contention_seen: bool = False

    @property
    def active(self) -> bool:
        return self.reference_mb is not None and self.used_at_gate_mb is not None

    def missing(self) -> None:
        """A sample without per-pid attribution of the card: no figure, and the run of over samples breaks."""
        self.run, self.run_contention = 0, False

    def observe(self, own_shared: Optional[int], own_dedicated: int, card_used: int) -> None:
        """One attributed sample of the launch's card (``own_shared`` None: no shared figure for it)."""
        others = max(0, card_used - own_dedicated)
        self.others_peak_mb = max(self.others_peak_mb or 0, others)
        if own_shared is None:
            self.missing()
            return
        self.samples += 1
        self.shared_peak_mb = max(self.shared_peak_mb or 0, own_shared)
        if not self.active or own_shared <= self.reference_mb + self.margin_mb:
            self.missing()
            return
        self.run += 1
        self.run_contention = self.run_contention or others > self.used_at_gate_mb + self.margin_mb
        if self.run < OVER_SAMPLES:
            return
        if not self.spilled_seen:
            logger.warning("Unreal job %s spills into shared memory: its own shared usage %s MB is over the reference "
                           "%s MB + %s MB margin (others on the card %s MB, %s MB at the gate); the job runs on.",
                           self.job_id, own_shared, self.reference_mb, self.margin_mb, others, self.used_at_gate_mb)
        self.spilled_seen = True
        self.contention_seen = self.contention_seen or self.run_contention

    @property
    def spilled(self) -> Optional[bool]:
        return None if not self.active or self.samples == 0 else self.spilled_seen

    @property
    def cause(self) -> Optional[str]:
        if self.spilled is not True:
            return None
        return CONTENTION if self.contention_seen else SELF


@dataclass(frozen=True)
class Spill:
    """A job's D9 outcome over its render launches (the memory report and the summary)."""
    spilled: Optional[bool] = None
    cause: Optional[str] = None
    shared_peak_mb: Optional[int] = None
    reference_mb: Optional[int] = None
    others_peak_mb: Optional[int] = None


def watch_for(gate, job_id) -> Callable[[], SpillWatch]:
    """A new ``SpillWatch`` per render launch, from the gate's result and the card's used at the gate."""
    result = getattr(gate, 'result', None)
    reference = result.shared_peak_mb if result is not None else None
    used_at_gate = getattr(gate, 'used_mb', None)
    margin = config_unreal.SPILL_MARGIN_MB  # read at call time (tests and the env override)
    return lambda: SpillWatch(reference_mb=reference, used_at_gate_mb=used_at_gate, margin_mb=margin, job_id=job_id)


def render_watches(runs) -> List[SpillWatch]:
    """The watches of the render launches in ``runs`` (``PhaseRun`` list), in launch order."""
    watches = [getattr(run.outcome.vram, 'spill', None) for run in runs if run.phase == 'render']
    return [watch for watch in watches if watch is not None]


def _largest(values) -> Optional[int]:
    values = [value for value in values if value is not None]
    return max(values) if values else None


def outcome(runs) -> Spill:
    """D9 over every render launch: spilled if any launch spilled, False if any said no, else None."""
    watches = render_watches(runs)
    states = [watch.spilled for watch in watches]
    spilled = True if True in states else False if False in states else None
    cause = None
    if spilled:
        cause = CONTENTION if any(watch.cause == CONTENTION for watch in watches) else SELF
    return Spill(spilled=spilled, cause=cause, shared_peak_mb=_largest(w.shared_peak_mb for w in watches),
                 reference_mb=watches[0].reference_mb if watches else None,
                 others_peak_mb=_largest(w.others_peak_mb for w in watches))


def shared_peak(runs) -> Optional[int]:
    """A pilot's ``shared_peak_mb`` (D9): its own largest shared usage on the card over its **last** render launch.

    The last render launch is the one whose log, CSV capture and driver the pilot reports (``pilot_flow``); an
    earlier launch of a render retry was cut short and its figures are not the pilot's. ``None`` without one.
    """
    renders = [run for run in runs if run.phase == 'render']
    watch = getattr(renders[-1].outcome.vram, 'spill', None) if renders else None
    return watch.shared_peak_mb if watch is not None else None


def _value(value) -> object:
    return '' if value is None else value


def summary_values(runs) -> Dict[str, object]:
    """FR-MT-S-2's summary keys (``''`` when unknown; ``spilled`` is 1, 0 or '')."""
    spill = outcome(runs)
    return {'spilled': '' if spill.spilled is None else (1 if spill.spilled else 0),
            'spill_cause': _value(spill.cause), 'spill_shared_peak_mb': _value(spill.shared_peak_mb),
            'spill_reference_mb': _value(spill.reference_mb), 'spill_others_peak_mb': _value(spill.others_peak_mb)}
