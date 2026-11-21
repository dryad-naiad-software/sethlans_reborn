# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The UE phases of an audio-only job (sequence rendering FR-SA-2..10, D14,
D23). ``AudioFlow`` is ``UeFlow`` with its hooks replaced:

* the prep runs in mode ``prepare_audio`` (one duplicate over ``[r0, end +
  1)``: image outputs off, the audio output kept, 128x72, 2 temporal
  samples) and must report the sequence's ``frame_rate``;
* the warm-up visits the run's first rendered frame and every camera-cut
  start inside the rendered range (FR-SW-6, at most 64);
* the render is the normal render on the job's card with the audio flags
  (``_render_extra``, FR-SA-4); its log is scanned for the streaming fault
  (fails the job, D14) and MRG's skipped latency compensation (information)
  before the normal render check;
* the output step (FR-SA-5): exactly one ``.wav`` and no image, opened with
  ``fs_safe.open_output_file`` and checked from that handle
  (``wav_check``); then the offset step (``audio_offset_step``, D23), which
  never fails the job.

The result's ``sound`` (``SoundOutput``) is what ``sequence_delivery``
uploads to ``sound-result/``; the summary carries ``audio_samples``,
``audio_rate`` and ``audio_channels`` (FR-SA-7) and the offset's status.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import List

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor import audio_offset_step, queue_prep, render_check, sequence_prep, wav_check
from sethlans_worker_agent.unreal_executor.job_flow import FlowResult, UeFlow
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure

MAX_WARM_FRAMES = 64
UNSAFE = "The sound render's .wav failed the output safety checks ({cls})."


@dataclass(frozen=True)
class SoundOutput:
    path: Path
    frames: int                 # the rendered frames (run and audio lead-in)
    rate_fraction: tuple        # the sequence's (num, den)
    info: wav_check.WavInfo
    offset: dict                # shared.unreal.sound_result's offset object
    latency_skipped: bool


def warm_frames(run, cut_starts) -> List[int]:
    """FR-SW-6 for an audio run: its first rendered frame and the cut starts it renders through; at most 64."""
    return sorted({run.first} | {cut for cut in cut_starts if run.first <= cut <= run.end})[:MAX_WARM_FRAMES]


class AudioFlow(UeFlow):
    _render_extra = {'audio': True}
    rate_fraction = (0, 1)
    latency_skipped = False

    def _prep_params(self) -> dict:
        job = self.job
        return sequence_prep.audio_params(self.params, job.output, job.prep_result, job.scripts)

    def _warm_frames(self, prep) -> List[int]:
        return warm_frames(self.params.audio, prep.camera_cut_starts)

    def prep(self) -> queue_prep.PrepResult:
        run = self.params.audio
        self.summary.update({'audio_run': f"{run.start}-{run.end}", 'audio_lead_in': run.lead})
        prep = super().prep()
        self.rate_fraction = sequence_prep.frame_rate(queue_prep.load(self.job.prep_result))
        return prep

    def _check_render(self, run, prep):
        facts = wav_check.scan_log(run.log_path, self.config.log_max_bytes)
        if facts.fault:
            raise JobFailure(wav_check.FAULT_MESSAGE)
        self.latency_skipped = facts.latency_skipped
        return render_check.check_render(run.outcome, run.log_path, prep.queue_jobs, self.config.log_max_bytes)

    def _collect(self, result: FlowResult, prep, run, before) -> FlowResult:
        audio = self.params.audio
        path = wav_check.collect(self.job.output)
        try:
            handle = fs_safe.open_output_file(path, path.parent)
        except OSError as exc:
            raise JobFailure(UNSAFE.format(cls=type(exc).__name__)) from exc
        with handle:
            info = wav_check.check_handle(handle, audio.rendered, self.rate_fraction, self.latency_skipped)
        self.summary.update({'audio_samples': info.samples, 'audio_rate': info.rate, 'audio_channels': info.channels,
                             'latency_compensation_skipped': self.latency_skipped})
        offset = audio_offset_step.measure(self, path, self.rate_fraction)
        self.summary.update({'sound_offset': offset['status'], 'sound_offset_samples': offset['offset_samples'],
                             'sound_offset_shots': f"{offset['shots_confident']}/{offset['shots_tried']}"})
        if offset['reason']:
            self.summary.set('sound_offset_reason', offset['reason'])
        result.outputs = [path]
        result.sound = SoundOutput(path=path, frames=audio.rendered, rate_fraction=self.rate_fraction, info=info,
                                   offset=offset, latency_skipped=self.latency_skipped)
        self.summary.set('output', path.name)
        return result
