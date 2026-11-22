# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A job's memory figures for its summary and the pilot's body (render per card FR-PC-RAM-3,
FR-PC-ATT-2), from the launches' ``RunOutcome.ram`` (``runner_ram.PhaseRam``) and ``VramTrace``.

* **Per phase** (``prep``, ``warmup``, ``render``): the largest figure over the phase's launches
  (a warm-up and render retry are launches of the same phase); unknown when any launch's figure is
  unknown (an outcome without figures counts as unknown). A phase that did not run has none.
* **Summary** (every Unreal job): ``ram_<phase>_ws_mb``, ``ram_<phase>_commit_mb`` (``''`` when
  unknown or not run), ``ram_warmup_cold`` (``1`` exactly when a ShaderCompileWorker was among a
  warm-up launch's Job Object members, ``RunOutcome.shader_workers``; UE starts them only when shader
  jobs are queued, so a new project version on a warm slot is warm, whatever its marker says), and
  the last render launch's ``vram_job_peak_mb`` and ``vram_job_other_cards_mb`` (``''`` when
  unknown), and ``vram_job_peak_phase``: ``warmup`` or ``render``, the launch that gave the memory
  report's ``job_peak_mb`` (``job_peak``; ``''`` without one).
* **Pilot body**: ``ram`` = ``{"prep": {"ws_mb", "commit_mb"}, "warmup": {..., "cold"} | None,
  "render": {...}}``, ``None`` when any figure of a phase that ran is unknown or prep or render did
  not run; ``ram_process_commit_peak_mb`` = the largest ``PeakProcessMemoryUsed`` over the launches,
  ``None`` when any is unknown. Each process peak is at most its launch's job peak, so the
  manager's rule (process peak <= the largest ``commit_mb``) holds.
"""
from typing import Dict, Iterable, Optional, Sequence, Tuple

from sethlans_worker_agent.unreal_executor.runner_ram import PhaseRam

PHASES = ('prep', 'warmup', 'render')
PEAK_PHASES = ('warmup', 'render')  # the launches the memory report's job_peak_mb covers (D6, Q11)


def _largest(values: Sequence[Optional[int]]) -> Optional[int]:
    return None if not values or any(value is None for value in values) else max(values)


def _launches(runs: Iterable, phase: Optional[str] = None) -> list:
    return [run.outcome.ram or PhaseRam() for run in runs if phase is None or run.phase == phase]


def phase_figures(runs: Sequence) -> Dict[str, Optional[PhaseRam]]:
    """Each phase's figures, or ``None`` for a phase that did not run."""
    out: Dict[str, Optional[PhaseRam]] = {}
    for phase in PHASES:
        launches = _launches(runs, phase)
        out[phase] = PhaseRam(ws_mb=_largest([ram.ws_mb for ram in launches]),
                              commit_mb=_largest([ram.commit_mb for ram in launches]),
                              process_commit_mb=_largest([ram.process_commit_mb for ram in launches])
                              ) if launches else None
    return out


def _value(value: Optional[int]):
    return '' if value is None else value


def warmup_cold(runs: Sequence) -> bool:
    """A warm-up launch compiled shaders: a ShaderCompileWorker was one of its members (D8)."""
    return any(run.outcome.shader_workers for run in runs if run.phase == 'warmup')


def summary_values(runs: Sequence) -> Dict[str, object]:
    """The FR-PC-RAM-3 and FR-PC-ATT-2 summary keys (empty ``runs``: every figure ``''``)."""
    values: Dict[str, object] = {}
    for phase, figures in phase_figures(runs).items():
        values[f'ram_{phase}_ws_mb'] = _value(figures.ws_mb if figures else None)
        values[f'ram_{phase}_commit_mb'] = _value(figures.commit_mb if figures else None)
    values['ram_warmup_cold'] = 1 if warmup_cold(runs) else 0
    trace = _last_render_trace(runs)
    known = trace is not None and not trace.job_unknown
    values['vram_job_peak_mb'] = _value(trace.job_peak_mb if known else None)
    values['vram_job_other_cards_mb'] = _value(trace.job_other_cards_mb if known else None)
    values['vram_job_peak_phase'] = job_peak(runs)[1]  # the launch the memory report's job_peak_mb came from
    return values


def _last_render_trace(runs: Sequence):
    renders = [run for run in runs if run.phase == 'render']
    return renders[-1].outcome.vram if renders else None


def job_peak_mb(runs: Sequence) -> Optional[int]:
    """The memory report's ``job_peak_mb`` (per-machine memory test D6, FR-MT-S-3); None when unknown.

    The job's own Job Object members' dedicated usage on its card (``VramTrace.max_job``, every attributed
    sample of the **whole** launch, start-up spike included, as the pilot's result peak includes its start-up),
    the largest over **every** warm-up and render launch (a failed first launch and a retry's warm-up count;
    Q11: a cold warm-up can peak above the render). Never the card's rise, which counts other programs too. A
    launch with only some samples attributed gives a lower bound, still valid for D6's feedback. Each trace
    holds the launch's card alone (``phases`` cuts every reading to it). The prep is never counted (no card
    of its own, not sampled for a job). The summary's ``vram_job_peak_mb`` (the last render launch, from its
    first output file on) is a different figure.
    """
    return job_peak(runs)[0]


def job_peak(runs: Sequence) -> Tuple[Optional[int], str]:
    """``(job_peak_mb(runs), the phase of the launch that gave it)``: ``'warmup'`` or ``'render'``.

    A tie goes to ``'render'``: the warm-up is named only when it peaked strictly above every render launch.
    ``(None, '')`` without a figure; a peak of 0 (nothing attributed, sent as null) has no phase either.
    """
    best: Tuple[Optional[int], str] = (None, '')
    for run in runs:
        if run.phase not in PEAK_PHASES:
            continue
        for peak in getattr(run.outcome.vram, 'max_job', {}).values():
            if best[0] is None or peak > best[0] or (peak == best[0] and run.phase == 'render'):
                best = (peak, run.phase)
    return best if best[0] else (best[0], '')


def _pair(figures: Optional[PhaseRam]) -> Optional[dict]:
    if figures is None or figures.ws_mb is None or figures.commit_mb is None:
        return None
    return {'ws_mb': figures.ws_mb, 'commit_mb': figures.commit_mb}


def pilot_ram(runs: Sequence) -> Tuple[Optional[dict], Optional[int]]:
    """``(ram, ram_process_commit_peak_mb)`` of the pilot-result body."""
    figures = phase_figures(runs)
    prep, render = _pair(figures['prep']), _pair(figures['render'])
    warmup = _pair(figures['warmup'])
    ram = None
    if prep is not None and render is not None and (figures['warmup'] is None or warmup is not None):
        ram = {'prep': prep, 'render': render,
               'warmup': None if warmup is None else dict(warmup, cold=warmup_cold(runs))}
    return ram, _largest([launch.process_commit_mb for launch in _launches(runs)])
