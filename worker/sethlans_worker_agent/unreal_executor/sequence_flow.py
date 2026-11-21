# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The UE phases of sequence rendering's job kinds (FR-SD-4, FR-SW-3..11;
``AudioFlow`` comes with sound).

``ShotsFlow`` (kind ``shots``) runs the prep commandlet in mode
``list_shots`` and nothing else: no warm-up, no render, no
``-graphicsadapter`` (the prep builder never passes one) and no GPU
memory sampling. It reuses ``UeFlow``'s constructor and quit notes; the
launch goes through ``PhaseRunner.launch`` like every other phase (the
pre-launch checks, the Zen verification, the cancel and shutdown rules).
The result is parsed strictly by ``sequence_prep.parse_shots``.

``RangeFlow`` (a piece: kind ``render`` with a frame range) is ``UeFlow``
with four hooks replaced:

* before the prep, the disk check (FR-SW-11): ``rendered frames x w x h x
  B`` bytes (``B`` 4 for PNG, 8 for EXR; ``[w, h]`` from
  ``unreal.output_size``) must fit in the cache drive's free space above
  ``MIN_FREE_GB``, else the piece is released (``local_conflict``) with a
  message naming both figures; without an output size it is skipped; the
  claim's ``disk_ledger`` (render per card D12, FR-PC-DSK-1) subtracts what
  the other running pieces have not written yet and counts as free only the
  frames of job folders no running claim owns;
* the prep runs in mode ``prepare_range`` (one duplicate over ``[start -
  lead, end + 1)``); the preset's own draft CVars (``draft_conflicts``)
  become summary warnings (FR-SW-5);
* the warm-up visits the frames the piece renders first in each shot
  (FR-SW-6, at most 64): the lead-in's first frame (the shot the lead-in
  starts in is not in ``unreal.piece_shots``, so the worker takes it from
  the range), each covered shot's first kept frame, and every camera-cut
  start inside the rendered range (from the prep);
* the render check accepts FR-SW-8's loads-in-flight exit;
* the output step checks every rendered frame, deletes the lead-in files
  (``output.collect_range``), and records the per-shot timings
  (``frame_timing``) and the card the piece rendered on.
"""
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional, Sequence

from sethlans_worker_agent.unreal_executor import (
    disk_ledger, frame_timing, job_dir, output, pilot, queue_prep, render_check, sequence_prep)
from sethlans_worker_agent.unreal_executor.disk_ledger import GIB
from sethlans_worker_agent.unreal_executor.job_flow import FlowResult, UeFlow
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure

MAX_WARM_FRAMES = 64
DRAFT_CONFLICT = "The preset sets {name} itself; the draft could not change it."
MOTION_BLUR_PATHS = ('samples1_pp_off',)


@dataclass
class ShotsResult:
    discovery: sequence_prep.Discovery
    prep_s: float


@dataclass(frozen=True)
class PieceOutput:
    frames: tuple                    # the kept frame numbers, in the order of FlowResult.outputs
    timing: frame_timing.PieceTiming
    vram_peak_mb: Optional[int]
    card: object = None              # the reading's GpuMem of the card it rendered on, or None (the override)


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


def warm_frames(piece, cut_starts: Sequence[int]) -> List[int]:
    """FR-SW-6: the first frame the piece renders in each shot it touches, lead-in included; at most 64."""
    frames = {piece.first} | {start for _index, start, _end in piece.shots}
    frames |= {cut for cut in cut_starts if piece.first <= cut <= piece.end}
    return sorted(frames)[:MAX_WARM_FRAMES]


def disk_need(piece, ext: str) -> Optional[int]:
    """FR-SW-11: the bytes a piece's frames can take (uncompressed RGBA), or None without an output size."""
    if piece.output_size is None:
        return None
    width, height = piece.output_size
    return piece.rendered * width * height * (8 if ext == 'exr' else 4)


def check_disk(piece, ext: str, output_dir, reserve_gb: int, disk, job_root=None) -> Optional[int]:
    """Raise ``LocalConflict`` when the piece's frames do not fit above the reserve; returns the estimate.

    ``disk`` is the claim's ``disk_ledger.DiskTarget``: the other running pieces' unwritten frames are
    subtracted and only job folders of no running claim count as free (D12). ``job_root`` is the
    claim's job folder (``None``: no folder, no leftovers looked for)."""
    need = disk_need(piece, ext)
    if need is None:
        return None
    return disk.check(job_root, need, output_dir, reserve_gb, frames=piece.rendered)


def card_of(choice, reading):
    """The reading's card the piece rendered on (by card key), or None (the override, or not found)."""
    try:
        return pilot.check_card(choice, reading)
    except JobFailure:
        return None


def _reserve_gb() -> int:
    from sethlans_worker_agent import config_unreal
    return config_unreal.MIN_FREE_GB


class RangeFlow(UeFlow):
    """A sequence piece: one Unreal launch over ``[start - lead, end]``; the lead-in frames are thrown away."""
    launched_at = 0.0
    reserve_gb = staticmethod(_reserve_gb)
    disk = None  # the claim's disk_ledger.DiskTarget (job_run); None: a ledger of this piece alone

    @property
    def ext(self) -> str:
        return 'exr' if self.params.output_format == 'EXR' else 'png'

    def _prep_params(self) -> dict:
        job = self.job
        return sequence_prep.range_params(self.params, job.output, job.prep_result, job.scripts)

    def _warm_frames(self, prep) -> List[int]:
        return warm_frames(self.params.piece, prep.camera_cut_starts)

    def prep(self) -> queue_prep.PrepResult:
        piece = self.params.piece
        disk = self.disk or disk_ledger.DiskTarget(disk_ledger.DiskLedger(), uuid.uuid4().hex)
        need = check_disk(piece, self.ext, self.job.output, self.reserve_gb(), disk, getattr(self.job, 'root', None))
        self.summary.update({'piece': f"{piece.start}-{piece.end}", 'lead_in': piece.lead,
                             'output_format': self.params.output_format,
                             'disk_need_gb': '' if need is None else round(need / GIB, 1)})
        prep = super().prep()
        try:
            data = queue_prep.load(self.job.prep_result)
            conflicts = sequence_prep.draft_conflicts(data, sequence_prep.sent_draft_cvars(self.params))
        except queue_prep.PrepError as exc:
            raise JobFailure(str(exc)) from exc
        for name in conflicts:
            self.summary.append('draft_conflicts', name)
            self.summary.warn(DRAFT_CONFLICT.format(name=name))
        if dict(self.params.seq.draft).get('motion_blur') is False:  # which path U-3 saw (edits module)
            self.summary.set('motion_blur_off', queue_prep.allowed_word((data or {}).get('motion_blur_off'),
                                                                        MOTION_BLUR_PATHS))
        return prep

    def render(self, prep, attempt: int):
        run, check, before = super().render(prep, attempt)
        self.launched_at = time.time() - run.outcome.wall_s  # the render process's start, on the files' clock
        return run, check, before

    def _check_render(self, run, prep):
        piece, job_id, out = self.params.piece, self.params.job_id, self.job.output
        check = render_check.check_range(
            run.outcome, run.log_path, lambda: output.range_complete(out, job_id, piece.first, piece.end, self.ext),
            self.config.log_max_bytes)
        if check.accepted:
            self.summary.set('exit_accepted', check.accepted_exit)
            self.summary.warn(render_check.ACCEPTED_WARNING.format(code=check.accepted_exit))
        return check

    def _collect(self, result: FlowResult, prep, run, before) -> FlowResult:
        piece, job_id = self.params.piece, self.params.job_id
        out = output.collect_range(self.job.output, job_id, piece.start, piece.end, piece.lead, self.ext)
        timing = frame_timing.piece_timing([(frame, mtime) for frame, _path, mtime in out.kept], out.lead_mtimes,
                                           self.launched_at, piece.shots)
        for text in timing.warnings:
            self.summary.warn(text)
        result.outputs = [path for _frame, path, _mtime in out.kept]
        result.piece = PieceOutput(frames=tuple(frame for frame, _path, _mtime in out.kept), timing=timing,
                                   vram_peak_mb=run.outcome.vram_peak_mb, card=card_of(self.choice, self.reading))
        self.summary.update({'output': f"{len(out.kept)} {self.ext.upper()} frames",
                             'lead_in_deleted': len(out.lead_mtimes)})
        return result
