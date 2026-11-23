# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Other programs' use added to a pilot's peak (Unreal per-machine memory test,
owner decision Q10, band 2; ``pilot_flow.check_others`` decides the band).

D6's verdict sum is ``peak + baseline`` (``pilot.measure``). Part of other
programs' use is already in it, so band 2 adds only the rest:

* ``top`` (``top_others``): the highest other-programs figure seen on the
  card, in MB, over every checked launch (one with a pre-launch reading of
  the card and per-process samples): ``VramTrace.max_others``, the card's
  ``used`` minus the launch's own processes' use on it.
* ``counted`` (``counted``): how much of theirs the sum already holds.
  A card peak's sum is the card's whole ``used`` at its peak sample, so it
  holds other programs' use at that sample (``VramTrace.others_at_max``).
  A CSV peak is UE's own figure; its sum holds the peak launch's baseline,
  ``min(pre, post)``, read while none of the pilot's processes ran (so all
  of it is other programs'). A card peak whose peak sample could not be
  split by process counts the baseline too.
* ``added = max(0, top - counted)``: the sum becomes the pilot's own peak
  plus the most other programs were seen using. A program opened in an
  earlier launch and left open is in the later launch's ``pre`` (so in the
  baseline) and in its peak sample, so it is not counted twice, and a card
  seen holding the pilot and the programs together under its 93% line
  keeps the verdict it measured.

``with_others(card, csv, added)`` puts ``added`` on the figure that set the
peak (the CSV peak when larger than the card delta, else the card delta),
so ``peak = max(card, csv)`` and ``peak_source`` still hold (the manager
checks both).
"""
from typing import Optional, Tuple


def top_others(runs) -> Optional[int]:
    """Q10: the highest other-programs figure over every checked launch; ``None`` when no launch was checked."""
    best = None
    for run in runs:
        for index, value in getattr(run.outcome.vram, 'max_others', {}).items():
            if run.pre is not None and index in run.pre:
                best = value if best is None else max(best, value)
    return best


def counted(card: int, csv: Optional[int], baseline: int, peak_run, index: int) -> int:
    """How much of other programs' use ``peak + baseline`` already holds (see the module docstring)."""
    if (csv or 0) > card:
        return baseline
    return getattr(peak_run.outcome.vram, 'others_at_max', {}).get(index, baseline)


def added(top: Optional[int], card: int, csv: Optional[int], baseline: int, peak_run, index: int) -> int:
    """Band 2's addition: ``max(0, top - counted)``; 0 without a ``top`` (bands 1 and 3)."""
    if top is None:
        return 0
    return max(0, top - counted(card, csv, baseline, peak_run, index))


def with_others(card: int, csv: Optional[int], amount: int) -> Tuple[int, Optional[int]]:
    """``amount`` MB on the figure that set the peak, so ``max(card, csv)`` grows by exactly that."""
    if amount and (csv or 0) > card:
        return card, csv + amount
    return card + amount, csv
