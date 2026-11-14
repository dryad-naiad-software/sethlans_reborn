# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Summary values from a render log scan (Unreal foundation FR-UE-12,
FR-UE-14, FR-UE-11 VRAM peak, FR-UE-21; OQ-7).

* ``cvars_applied=<n>/<m>``: ``n`` distinct ``dp_cvar_set`` names in the
  merged set, ``m`` the merged set size;
* ``cvar_collisions``: ``mrq_cvar_applied`` names that are also in the
  merged ``-DPCVars`` set (each also a ``cvar_collision=<name>`` warning);
* ``streaming_pool_mb`` / ``rt_pool_mb``: the value ``cvar_priority_ignored``
  reports as held, else the merged value;
* ``vram_peak_ratio_pct`` and ``vram_peak_over_measurement`` for a normal
  job (FR-UE-11, FR-UE-14).
"""
from typing import Dict, List, Sequence, Tuple

from sethlans_worker_agent.unreal_executor import cvars
from shared.unreal import vram_gate

POOL_SUMMARY = (('r.Streaming.PoolSize', 'streaming_pool_mb'),
                ('r.RayTracing.ResidentGeometryMemoryPoolSizeInMB', 'rt_pool_mb'))


def _held(scan, name: str) -> str:
    for groups in reversed(scan.captures.get('cvar_priority_ignored', [])):
        if len(groups) >= 2 and groups[0].casefold() == name.casefold():
            return cvars.sanitize_value(groups[1])
    return ''


def render_values(scan, merged: Sequence[Tuple[str, str]]) -> Tuple[Dict[str, object], List[str]]:
    """``(summary values, warnings)`` for the final render attempt."""
    folded = {name.casefold(): name for name, _value in merged}
    applied = {n.casefold() for n in scan.captured_names('dp_cvar_set') if n.casefold() in folded}
    collisions = sorted({folded[n.casefold()] for n in scan.captured_names('mrq_cvar_applied')
                         if n.casefold() in folded})
    values: Dict[str, object] = {
        'mrq_duration_s': '' if scan.mrq_duration_s() is None else round(scan.mrq_duration_s(), 1),
        'mrq_executor_jobs': '' if scan.executor_jobs() is None else scan.executor_jobs(),
        'compilation_cancelled': scan.count('compilation_cancelled'),
        'memory_over_budget': scan.count('memory_over_budget'),
        'gpu_errors': scan.count('gpu_device_lost'),
        'python_errors': scan.count('python_error'),
        'ensure_failed': scan.count('ensure_failed'),
        'compute_framework_errors': scan.count('compute_framework_error'),
        'cvars': cvars.dpcvars_string(merged),
        'cvars_applied': f"{len(applied)}/{len(merged)}",
        'cvar_collisions': ','.join(collisions),
    }
    merged_dict = dict(merged)
    for name, key in POOL_SUMMARY:
        values[key] = _held(scan, name) or next((v for n, v in merged_dict.items() if n.casefold() == name.casefold()),
                                                '')
    return values, [f"cvar_collision={name}" for name in collisions]


def vram_values(params, peak_mb) -> Dict[str, object]:
    values: Dict[str, object] = {'vram_peak_mb': '' if peak_mb is None else peak_mb, 'vram_peak_source': 'card'}
    if params.is_pilot or peak_mb is None or not params.measured_peak_mb:
        return values
    values['vram_peak_ratio_pct'] = round(100 * peak_mb / params.measured_peak_mb)
    if peak_mb > vram_gate.measured_need_mb(params.measured_peak_mb):
        values['vram_peak_over_measurement'] = 1
    return values
