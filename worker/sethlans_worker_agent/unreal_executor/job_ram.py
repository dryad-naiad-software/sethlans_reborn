# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
An Unreal job's RAM need (render per card FR-PC-RAM-4, D8, D9).

``parse_measured(raw)`` checks one ``unreal_machine_results[].ram`` of the poll payload (per-machine
memory test D11: the ``ram`` of this machine's result for one card kind, or null), the shape the
manager's ``UnrealRamSerializer`` stores::

    {"prep":   {"ws_mb": n, "commit_mb": n},
     "warmup": {"ws_mb": n, "commit_mb": n, "cold": bool} | null,
     "render": {"ws_mb": n, "commit_mb": n}}

Exactly those keys, every figure a strict integer ``1..MAX_PEAK_MB``; anything else raises
``ValueError`` (``job_params`` turns it into a ``JobDataError`` naming the field).

``need(measured, warmup_planned, card_total_mb, default_ws_mb, default_commit_mb)`` is the
maximum, per kind, over the phases the job will run: prep and render, plus the warm-up when one is
planned (its slot's marker is absent or stale, ``warmup.decide``). ``measured`` is the chosen card's
result ``ram`` (FR-MT-G-3, ``reserve_local.measured_ram``). Parts with no measurement use the
defaults: no measurement at all (pilots, ``shots``, a result without RAM figures), or a planned warm-up
whose measured warm-up was not cold (or did not run). Default working set ``default_ws_mb``;
default commit the larger of ``default_commit_mb`` and ``default_ws_mb + card_total_mb`` (commit is
about the working set plus the process's GPU memory, D8, so a larger card needs more commit).
``render_ws_mb``/``render_commit_mb`` are the render phase's figure (measured, else the default): the
claim's need once its warm-up's process tree has exited (``runner_ram``), so a cold warm-up's peak does
not hold back a second job for the whole render.
"""
from dataclasses import dataclass
from typing import Optional

from shared.unreal.vram_gate import MAX_PEAK_MB

PHASE_KEYS = frozenset({'ws_mb', 'commit_mb'})
WARMUP_KEYS = PHASE_KEYS | {'cold'}
RAM_KEYS = frozenset({'prep', 'warmup', 'render'})


@dataclass(frozen=True)
class PhaseRam:
    ws_mb: int
    commit_mb: int


@dataclass(frozen=True)
class MeasuredRam:
    prep: PhaseRam
    warmup: Optional[PhaseRam]
    warmup_cold: bool
    render: PhaseRam


@dataclass(frozen=True)
class RamNeed:
    ws_mb: int
    commit_mb: int
    source: str          # 'measured', 'default' or 'measured+default'
    warmup: bool         # a warm-up was planned
    render_ws_mb: int = 0      # the render phase's figure: the need once a warm-up has ended
    render_commit_mb: int = 0


def _figure(value, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PEAK_MB:
        raise ValueError(f"{where} must be an integer in [1, {MAX_PEAK_MB}]")
    return value


def _keys(raw, keys: frozenset, where: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    if set(raw) != keys:
        raise ValueError(f"{where} must have exactly the keys {sorted(keys)}")
    return raw


def _phase(raw, where: str, keys: frozenset = PHASE_KEYS) -> PhaseRam:
    raw = _keys(raw, keys, where)
    return PhaseRam(_figure(raw['ws_mb'], f"{where}.ws_mb"), _figure(raw['commit_mb'], f"{where}.commit_mb"))


def parse_measured(raw) -> Optional[MeasuredRam]:
    """The checked RAM figures of one result, or ``None`` (null); raises ``ValueError``."""
    if raw is None:
        return None
    raw = _keys(raw, RAM_KEYS, 'ram')
    warmup, cold = None, False
    if raw['warmup'] is not None:
        warmup = _phase(raw['warmup'], 'warmup', WARMUP_KEYS)
        cold = raw['warmup']['cold']
        if not isinstance(cold, bool):
            raise ValueError("warmup.cold must be true or false")
    return MeasuredRam(_phase(raw['prep'], 'prep'), warmup, cold, _phase(raw['render'], 'render'))


def need(measured: Optional[MeasuredRam], warmup_planned: bool, card_total_mb: int, default_ws_mb: int,
         default_commit_mb: int) -> RamNeed:
    """FR-PC-RAM-4: the job's physical and commit need in MB."""
    if isinstance(card_total_mb, bool) or not isinstance(card_total_mb, int) or card_total_mb < 0:
        raise ValueError(f"a card size must be a non-negative integer, not {card_total_mb!r}")
    default = PhaseRam(default_ws_mb, max(default_commit_mb, default_ws_mb + card_total_mb))
    if measured is None:
        return RamNeed(default.ws_mb, default.commit_mb, 'default', warmup_planned, default.ws_mb, default.commit_mb)
    phases = [measured.prep, measured.render]
    source = 'measured'
    if warmup_planned:
        cold = measured.warmup is not None and measured.warmup_cold
        phases.append(measured.warmup if cold else default)
        source = 'measured' if cold else 'measured+default'
    return RamNeed(max(p.ws_mb for p in phases), max(p.commit_mb for p in phases), source, warmup_planned,
                   measured.render.ws_mb, measured.render.commit_mb)
