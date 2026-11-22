# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A job's ``unreal_machine_results`` (Unreal per-machine memory test D2, D7,
D11, FR-MT-G-1).

The manager sends, on a render or audio job, this worker's own current
``fits`` results of the job's key (an audio job: its submission's piece
key), one per card kind::

    [{"measurement_id": int, "card_kind": "vvvv:dddd:rrrr", "peak_mb": int,
      "ram": {...} | null, "shared_peak_mb": int | null, "driver_version": str | null}]

``parse(kind, raw)`` checks it (the field name in every error is
``unreal_machine_results``):

* a ``render`` or ``audio`` job: a list of at most ``MAX_RESULTS`` objects
  (``[]`` when the worker has no result: the gate then finds no card);
* a ``pilot`` or ``shots`` job: null, absent or ``[]`` (they have no
  result);
* each entry has exactly the six keys; ``measurement_id`` an int > 0;
  ``card_kind`` matches ``KIND_RE`` and is unique in the list; ``peak_mb``
  an int in ``[1, MAX_PEAK_MB]`` (the result peak); ``ram`` null or the
  figures ``job_ram.parse_measured`` accepts; ``shared_peak_mb`` null or an
  int in ``[0, MAX_PEAK_MB]``; ``driver_version`` null or a string of at
  most ``MAX_DRIVER_LENGTH`` characters.

Anything else raises ``ValueError`` (``job_params`` turns it into a
``JobDataError``). ``bool`` is never an int here.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

from sethlans_worker_agent.unreal_executor import job_ram
from shared.unreal.card_kind import is_card_kind
from shared.unreal.vram_gate import MAX_PEAK_MB

FIELD = 'unreal_machine_results'
MAX_RESULTS = 4
MAX_DRIVER_LENGTH = 64
KEYED_KINDS = ('render', 'audio')
ENTRY_KEYS = frozenset({'measurement_id', 'card_kind', 'peak_mb', 'ram', 'shared_peak_mb', 'driver_version'})


@dataclass(frozen=True)
class MachineResult:
    """One card kind's current ``fits`` result on this worker for the job's key."""
    measurement_id: int
    card_kind: str
    peak_mb: int
    ram: Optional[job_ram.MeasuredRam]
    shared_peak_mb: Optional[int]
    driver_version: Optional[str]


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _entry(raw, where: str) -> MachineResult:
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    if set(raw) != ENTRY_KEYS:
        raise ValueError(f"{where} must have exactly the keys {sorted(ENTRY_KEYS)}")
    measurement, kind, peak = raw['measurement_id'], raw['card_kind'], raw['peak_mb']
    shared, driver = raw['shared_peak_mb'], raw['driver_version']
    if not _is_int(measurement) or measurement < 1:
        raise ValueError(f"{where}.measurement_id must be an integer > 0")
    if not is_card_kind(kind):
        raise ValueError(f"{where}.card_kind must be a card kind (vvvv:dddd:rrrr)")
    if not _is_int(peak) or not 1 <= peak <= MAX_PEAK_MB:
        raise ValueError(f"{where}.peak_mb must be an integer in [1, {MAX_PEAK_MB}]")
    if shared is not None and (not _is_int(shared) or not 0 <= shared <= MAX_PEAK_MB):
        raise ValueError(f"{where}.shared_peak_mb must be null or an integer in [0, {MAX_PEAK_MB}]")
    if driver is not None and (not isinstance(driver, str) or len(driver) > MAX_DRIVER_LENGTH):
        raise ValueError(f"{where}.driver_version must be null or a string of at most {MAX_DRIVER_LENGTH} characters")
    try:
        ram = job_ram.parse_measured(raw['ram'])
    except ValueError as exc:
        raise ValueError(f"{where}.ram: {exc}") from None
    return MachineResult(measurement, kind, peak, ram, shared, driver)


def parse(kind: str, raw) -> Tuple[MachineResult, ...]:
    """FR-MT-G-1: the checked results of a job of ``kind``; raises ``ValueError``."""
    if kind not in KEYED_KINDS:
        if raw not in (None, []):
            raise ValueError(f"must be null for a {kind} job")
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"a {kind} job needs a list of this machine's results")
    if len(raw) > MAX_RESULTS:
        raise ValueError(f"at most {MAX_RESULTS} results")
    results = tuple(_entry(entry, f"[{index}]") for index, entry in enumerate(raw))
    kinds = [result.card_kind for result in results]
    if len(set(kinds)) != len(kinds):
        raise ValueError("a card kind is listed twice")
    return results


def for_kind(results: Tuple[MachineResult, ...], kind: Optional[str]) -> Optional[MachineResult]:
    """The result of card kind ``kind``, or ``None`` (no kind, or no result of it)."""
    if kind is None:
        return None
    return next((result for result in results if result.card_kind == kind), None)
