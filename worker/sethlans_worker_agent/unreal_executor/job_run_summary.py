# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The summary values ``job_run.JobRun`` sets before the job starts and after its phases ran
(Unreal foundation FR-UE-2; render per card FR-PC-SLOT-8, FR-PC-RAM-3, FR-PC-ATT-2; per-machine memory test
FR-MT-S-2's spill keys).
"""
from sethlans_worker_agent.unreal_executor import job_memory, spill, user_state


def start(summary, reservation) -> None:
    """The held card and the slot (FR-PC-SLOT-8, no ``exclusive`` since the per-card hold), the gate's values."""
    p = reservation.params
    summary.update({'engine': p.engine_build, 'kind': p.kind, 'card': reservation.card_key, 'slot': reservation.slot,
                    'user_state': user_state.SUMMARY.split('=', 1)[1], 'quit_forced': '', 'cvar_overridden': '',
                    'job_object': 'skipped'})
    summary.update(reservation.gate.summary)
    summary.update(job_memory.summary_values(()))  # every Unreal job carries the keys


def from_runner(summary, runner) -> None:
    """What the phase runner saw: profile resets, user cvars, Zen, Job Objects, the measured memory."""
    summary.update({'user_profile_reset': runner.profile_reset, 'user_cvars': runner.user_cvars,
                    'zen': f"private:{runner.config.zen_port}" if runner.zen_ok else 'FAILED'})
    if runner.runs:  # 'skipped' (the default) when no UE process was launched (review L4)
        ok = all(r.outcome.job_object == 'ok' for r in runner.runs)
        summary.set('job_object', 'ok' if ok else 'failed')
    for name in runner.collisions:
        summary.append('user_cvar_collision', name)
    summary.update(job_memory.summary_values(runner.runs))
    summary.update(spill.summary_values(runner.runs))  # per-machine memory test FR-MT-S-2 (D9)
