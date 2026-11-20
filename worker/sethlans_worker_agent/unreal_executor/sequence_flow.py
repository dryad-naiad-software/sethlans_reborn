# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The UE phases of sequence rendering's job kinds (FR-SD-4; ``RangeFlow`` and
``AudioFlow`` come with pieces and sound).

``ShotsFlow`` (kind ``shots``) runs the prep commandlet in mode
``list_shots`` and nothing else: no warm-up, no render, no
``-graphicsadapter`` (the prep builder never passes one) and no GPU
memory sampling. It reuses ``UeFlow``'s constructor and quit notes; the
launch goes through ``PhaseRunner.launch`` like every other phase (the
pre-launch checks, the Zen verification, the cancel and shutdown rules).
The result is parsed strictly by ``sequence_prep.parse_shots``.
"""
from dataclasses import dataclass

from sethlans_worker_agent.unreal_executor import job_dir, queue_prep, render_check, sequence_prep
from sethlans_worker_agent.unreal_executor.job_flow import UeFlow
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure


@dataclass
class ShotsResult:
    discovery: sequence_prep.Discovery
    prep_s: float


class ShotsFlow(UeFlow):

    def run(self) -> ShotsResult:
        params, job = self.params, self.job
        queue_prep.write_json(job.params, sequence_prep.shots_params(params, job.prep_result, job.scripts))
        launch = self.builders.build_prep(self.ctx, self.uproject, job.script(job_dir.PREP_SCRIPT),
                                          job.log('prep', 1), job.params, params.merged)
        run = self.runner.launch('prep', 1, launch, success_check=lambda: queue_prep.result_ok(job.prep_result),
                                 sample=False)
        self.prep_s = run.outcome.wall_s
        self.summary.set('prep_s', round(self.prep_s, 1))
        self._note_quit(run, 'prep')
        render_check.check_prep(run.outcome)
        data = queue_prep.load(job.prep_result)
        if data is None and run.outcome.exit_code not in (0, None):
            raise JobFailure(f"Unreal prep pass failed (exit code {run.outcome.exit_code}).")
        discovery = sequence_prep.parse_shots(data)
        for text in discovery.warnings:
            self.summary.warn(text)
        facts = discovery.preset_facts
        self.summary.update({'preset_kind': discovery.preset_kind, 'game_overrides': facts['game_overrides'],
                             'texture_streaming': facts['texture_streaming'], 'shot_source': discovery.shot_source,
                             'shots': len(discovery.shots), 'frame_rate': '/'.join(map(str, discovery.frame_rate)),
                             'playback_range': '-'.join(map(str, discovery.playback_range)), 'warmup': 'skipped'})
        return ShotsResult(discovery, self.prep_s)
