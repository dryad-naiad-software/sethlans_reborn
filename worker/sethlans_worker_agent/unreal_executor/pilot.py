# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pilot render metrics, worker side (Unreal foundation FR-UE-25, D2;
Dom R3-1..4, Conc R3-M7, R3-L6, Sec R3-F1).

``PILOT_CSV_CAPTURE_FRAMES`` is a frame count far above any pilot's
length, so the CSV capture covers the whole render and ends when UE exits.

``measure(runs, csv_peak)`` turns the card samples of every pilot phase
into the measurement. Every trace holds the job's card only (D22,
FR-GU-11: ``phases`` cuts each reading to it), so every rule below sees
that card alone:

* **per-phase baselines**: ``baseline(phase, gpu) = min(pre-launch reading,
  post-exit reading)``;
* ``card_delta_peak_mb`` = max over phases, GPUs and samples of ``used -
  baseline`` (>= 0), with its phase, GPU and baseline; ``baseline_drift_mb``
  = the largest ``|pre - post|``;
* ``peak_mb = max(card_delta_peak_mb, csv_local_used_peak_mb or 0)``,
  ``peak_source`` the larger (``card`` on a tie);
* ``process_peak_mb``: the largest memory of the job's own Job Object
  members on the peak's GPU (``VramTrace.max_job``, render per card
  FR-PC-ATT-2; summary ``pilot_process_peak_mb`` only, never reported).

Rejections: saturation (any sample ``>= floor(total * 0.93)``; the reason
names the card size), an implausible peak (< ``MIN_PLAUSIBLE_PEAK_MB``)
-> ``JobFailure``; freed memory (a sample more than
``PILOT_BASELINE_DROP_MB`` below the phase baseline) or a sampling gap
(> ``PILOT_MAX_SAMPLE_GAP_S``) -> ``LocalConflict('local_conflict')``.

``check_card(choice, reading)`` (FR-GU-4): the card the launch rendered on
(``adapter_choice.Choice.key``) must be in the GPU memory reading, by card
key; the launch check of UE's chosen index and ids is ``adapter_log``'s.
``chosen_adapter(log)`` reads UE's name for the D3D12 adapter it chose
(5.8.4 ``D3D12RHI/Private/Windows/WindowsD3D12Device.cpp``: ``:873``
"Found D3D12 adapter %u: %ls (VendorId: ..." and ``:942`` "Chosen D3D12
Adapter Id = %u"), for the ``pilot_adapter`` summary value only.
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from sethlans_worker_agent import report_limits
from sethlans_worker_agent.cards.identity import key_address
from sethlans_worker_agent.unreal_executor.log_tail import iter_log_lines
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure, LocalConflict
from shared.unreal import render_context, vram_gate

PILOT_CSV_CAPTURE_FRAMES = 5000000
PILOT_CSV_FLAGS = (f"-csvCaptureFrames={PILOT_CSV_CAPTURE_FRAMES}", "-csvGpuStats")
PILOT_BASELINE_DROP_MB = 512
PILOT_MAX_SAMPLE_GAP_S = 10.0
MIN_PLAUSIBLE_PEAK_MB = vram_gate.MIN_PLAUSIBLE_PEAK_MB
PILOT_SUMMARY_MAX_BYTES = 32 * 1024  # JSON size; also the manager's UTF-8 limit on ``summary`` (#260)

# Anchored to the line start (UE's optional "[time][frame]" prefix) with bounded digits, so a project ini
# value that UE echoes inside another line ("LogConfig: Set CVar [[...]]") does not match (chunk 7 security).
_UE_PREFIX = r'^(?:\[[^\]]*\]\[\s*\d+\])?'
FOUND_ADAPTER = re.compile(_UE_PREFIX + r'LogD3D12RHI: Found D3D12 adapter ([0-9]{1,2}): (.{1,200}?) '
                           r'\(VendorId: ([0-9a-fA-F]{1,4})(?:, DeviceId: ([0-9a-fA-F]{1,4}))?', re.ASCII)
CHOSEN_ADAPTER = re.compile(_UE_PREFIX + r'LogD3D12RHI: Chosen D3D12 Adapter Id = ([0-9]{1,2})\s*$', re.ASCII)
FOUND_REVISION = re.compile(r', Revision: ([0-9a-fA-F]{1,4})', re.ASCII)
FREED_MESSAGE = "GPU memory was freed by another program during the pilot; the measurement is unreliable."
GAP_MESSAGE = "GPU memory sampling stalled for {n} s during the pilot; the measurement is unreliable."


@dataclass(frozen=True)
class PilotMetrics:
    peak_mb: int
    peak_source: str
    card_delta_peak_mb: int
    csv_local_used_peak_mb: Optional[int]
    baseline_used_mb: int
    baseline_drift_mb: int
    peak_phase: str
    gpu_index: int
    max_sample_gap_s: float
    process_peak_mb: Optional[int] = None  # largest memory of the job's own processes on the peak's GPU (summary only)


def _process_peak(runs: Sequence, index: int) -> Optional[int]:
    values = [getattr(run.outcome.vram, 'max_job', {}).get(index) for run in runs]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _baselines(run) -> Dict[int, int]:
    pre, post = run.pre or {}, run.post or {}
    out = {}
    for index in set(pre) | set(post):
        values = [v for v in (pre.get(index), post.get(index)) if v is not None]
        out[index] = min(values)
    return out


def _check_saturation(run) -> None:
    trace = run.outcome.vram
    for index, used in trace.max_used.items():
        total = trace.total.get(index)
        budget = vram_gate.usable_mb(total, 0) if total else None  # floor(total * fraction), shared
        if budget is not None and used >= budget:
            raise JobFailure(f"Pilot render failed: the GPU was saturated ({used} of {budget} MB usable on a "
                             f"{total} MB card); the measurement is a lower bound.")


def _check_reliable(run, baselines: Dict[int, int]) -> float:
    trace = run.outcome.vram
    if run.pre is None or trace.samples == 0:
        raise LocalConflict('local_conflict', GAP_MESSAGE.format(n=int(run.outcome.wall_s)))
    for index, low in trace.min_used.items():
        if index in baselines and low < baselines[index] - PILOT_BASELINE_DROP_MB:
            raise LocalConflict('local_conflict', FREED_MESSAGE)
    if trace.max_gap_s > PILOT_MAX_SAMPLE_GAP_S:
        raise LocalConflict('local_conflict', GAP_MESSAGE.format(n=int(trace.max_gap_s)))
    return trace.max_gap_s


def measure(runs: Sequence, csv_peak_mb: Optional[int]) -> PilotMetrics:
    """FR-UE-25 "Peak metric" and its rejection rules over every pilot phase."""
    best = None  # (delta, phase, gpu, baseline)
    drift, gap = 0, 0.0
    for run in runs:
        _check_saturation(run)
        baselines = _baselines(run)
        gap = max(gap, _check_reliable(run, baselines))
        for index in set(run.pre or {}) & set(run.post or {}):
            drift = max(drift, abs(run.pre[index] - run.post[index]))
        for index, used in run.outcome.vram.max_used.items():
            delta = max(0, used - baselines.get(index, used))
            if best is None or delta > best[0]:
                best = (delta, run.phase, index, baselines.get(index, used))
    if best is None:
        raise LocalConflict('local_conflict', GAP_MESSAGE.format(n=0))
    card, phase, index, baseline = best
    peak = max(card, csv_peak_mb or 0)
    source = 'csv' if (csv_peak_mb or 0) > card else 'card'
    if peak < MIN_PLAUSIBLE_PEAK_MB:
        raise JobFailure(f"Pilot render failed: implausible GPU memory peak ({peak} MB).")
    return PilotMetrics(peak_mb=peak, peak_source=source, card_delta_peak_mb=card, csv_local_used_peak_mb=csv_peak_mb,
                        baseline_used_mb=baseline, baseline_drift_mb=drift, peak_phase=phase, gpu_index=index,
                        max_sample_gap_s=round(gap, 2), process_peak_mb=_process_peak(runs, index))


def chosen_adapter(log_path) -> Optional[str]:
    """The name of the D3D12 adapter UE chose, or None when the log does not say."""
    found: Dict[str, str] = {}
    chosen = None
    for line in iter_log_lines(log_path):
        match = FOUND_ADAPTER.search(line)
        if match:
            found.setdefault(match.group(1), match.group(2).strip())
            continue
        match = CHOSEN_ADAPTER.search(line)
        if match and chosen is None:
            chosen = match.group(1)
    return found.get(chosen) if chosen is not None else None


def check_card(choice, reading):
    """FR-GU-4: the reading's card whose key is the launch's card (``choice.key``); ``JobFailure`` otherwise."""
    key = choice.key if choice is not None else None
    address = key_address(key)
    for gpu in reading.gpus if reading is not None and address is not None else ():
        if key_address(gpu.card_key) == address:
            return gpu
    raise JobFailure(f"Pilot render failed: the card Unreal rendered on ({key or 'unknown'}) is not in the GPU "
                     "memory reading.")


def report_body(metrics: PilotMetrics, gpu, *, prep_s: float, warmup_s: float, render_s: float,
                frames: int, engine_build: str, overrides_sha256: str, summary: str,
                game_overrides: str = render_context.UNKNOWN,
                texture_streaming: str = render_context.UNKNOWN, ram: Optional[dict] = None,
                ram_process_commit_peak_mb: Optional[int] = None, shared_machine: bool = False) -> dict:
    """The FR-PM-4 ``pilot-result/`` body (strict schema; ``per_shot`` is left empty).

    ``ram``, ``ram_process_commit_peak_mb`` and ``shared_machine`` (per-card FR-PC-RAM-3) are always
    sent: ``ram`` is ``{"prep": {"ws_mb", "commit_mb"}, "warmup": {"ws_mb", "commit_mb", "cold"} | None,
    "render": {"ws_mb", "commit_mb"}}`` or ``None`` when any figure is unknown (``job_memory.pilot_ram``
    over the runner's figures). ``shared_machine`` stays False until the per-card hold (chunk 4a).

    ``game_overrides`` and ``texture_streaming`` are the prep's allowlisted words (``queue_prep``,
    FR-GC-4); the manager requires both (FR-GC-8), so they are always sent, ``unknown`` included.

    ``summary`` is tail-bounded to ``PILOT_SUMMARY_MAX_BYTES`` of JSON (#260): 32 KiB of UTF-8 can
    triple as JSON escapes, and the manager rejects request bodies over 128 KiB. A string's UTF-8 size
    is at most its JSON size minus the quotes, so the bounded summary also passes the manager's own
    32 KiB UTF-8 check on ``summary``.
    """
    return {
        'peak_mb': metrics.peak_mb, 'peak_source': metrics.peak_source,
        'card_delta_peak_mb': metrics.card_delta_peak_mb, 'baseline_used_mb': metrics.baseline_used_mb,
        'baseline_drift_mb': metrics.baseline_drift_mb, 'peak_phase': metrics.peak_phase,
        'max_sample_gap_s': min(float(metrics.max_sample_gap_s), PILOT_MAX_SAMPLE_GAP_S),
        'prep_s': round(prep_s, 1), 'warmup_s': round(warmup_s, 1), 'warmup_ran': True,
        'render_s': round(render_s, 1), 'frames': int(frames),
        'seconds_per_frame': round(render_s / frames, 2) if frames else 0.0, 'per_shot': [],
        # The manager matches the GPU to its stored reading by card_key, never by index (FR-GN-5, FR-GH-10).
        'gpu': {'index': gpu.index, 'name': gpu.name, 'total_mb': gpu.total_mb, 'card_key': gpu.card_key},
        'engine_build': engine_build, 'csv_local_used_peak_mb': metrics.csv_local_used_peak_mb,
        'overrides_sha256': overrides_sha256, 'summary': report_limits.bounded_tail(summary, PILOT_SUMMARY_MAX_BYTES),
        'game_overrides': game_overrides, 'texture_streaming': texture_streaming,  # what it ran with (FR-GC-8)
        'ram': ram, 'ram_process_commit_peak_mb': ram_process_commit_peak_mb, 'shared_machine': shared_machine,
    }


def summary_values(metrics: PilotMetrics, frames: List[int], adapter: str, cvars_sha: str) -> Dict[str, object]:
    return {'pilot_frames': len(frames), 'pilot_peak_mb': metrics.peak_mb,
            'pilot_baseline_mb': metrics.baseline_used_mb, 'pilot_peak_source': metrics.peak_source,
            'pilot_card_delta_peak_mb': metrics.card_delta_peak_mb,
            'pilot_csv_peak_mb': '' if metrics.csv_local_used_peak_mb is None else metrics.csv_local_used_peak_mb,
            'pilot_peak_phase': metrics.peak_phase, 'pilot_baseline_drift_mb': metrics.baseline_drift_mb,
            'pilot_max_sample_gap_s': metrics.max_sample_gap_s, 'pilot_adapter': adapter,
            'pilot_process_peak_mb': '' if metrics.process_peak_mb is None else metrics.process_peak_mb,
            'pilot_cvars_sha256': cvars_sha}
