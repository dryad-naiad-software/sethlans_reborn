# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Turning a finished Unreal job into a ``RenderResult`` (Unreal foundation
FR-UE-13, FR-UE-16, FR-UE-17, FR-UE-25 "Report"; chunk 7 req. 6, 8, 12,
15).

Runs **after** exit confirmation and the FR-PA-16 integrity check:

* success, normal job: an integrity failure discards the output; else the
  PNG is opened with ``fs_safe.open_output_file`` and uploaded **from that
  handle** (the bytes uploaded are the bytes checked), then deleted; the
  lifecycle reports ``DONE`` (``output_path=None``);
* success, pilot: ``pilot-result/``; 200 -> ``released=True``; 409/404 ->
  terminal, ``released=True``; 5xx after the retries -> ERROR logged, job
  left ``RENDERING`` (``released=True``); another 4xx -> ``ERROR`` with
  ``"Pilot result rejected by the manager: <detail>"``; a measured card
  without a card key is not reported (the manager could not match it):
  ``release/`` with ``local_conflict``, never a project failure (a
  backstop: the reserver already refuses such a pilot, FR-GU-10);
* success, sequence kinds: ``sequence_delivery`` (``shots``: ``shots-result/``,
  FR-SD-8; a piece: ``frames/`` per kept frame, then ``piece-result/``,
  FR-SW-10);
* failure -> ``ERROR`` (pilot: ``"Pilot render failed: <reason>"``);
* local conflict -> ``release/`` with its reason; share failure ->
  ``release/`` with ``share_unavailable`` and ``mark_unreachable``;
* ``canceled`` -> ``CANCELED``; ``shutdown`` -> ``release/`` with
  ``shutdown``; ``reassigned`` -> nothing reported;
* ``worker_hardware_changed`` (the card the job renders on was lost,
  changed or held, C-24 m) -> ``release/`` with ``worker_hardware_changed``
  (accepted by the manager from the same change on).
"""
import logging
import os
from dataclasses import dataclass
from typing import Callable, Optional

from sethlans_worker_agent.api_conflict import JobNoLongerAssigned
from sethlans_worker_agent.blender_job_stop import WORKER_HARDWARE_CHANGED
from sethlans_worker_agent.executors.base import RenderResult
from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor import pilot, sequence_delivery
from sethlans_worker_agent.unreal_executor.outcomes import pilot_message

logger = logging.getLogger(__name__)

INTEGRITY_MESSAGE = "The pristine project cache failed its integrity check after the render; output discarded."
UPLOAD_MESSAGE = "Unreal output upload failed."
HARDWARE_MESSAGE = "the graphics card it renders on can no longer be used"
NO_CARD_KEY_MESSAGE = ("the measured graphics card has no PCI address in this worker's GPU memory reading, so the "
                       "manager cannot match it; the pilot result is not sent and the job goes back to the queue.")


@dataclass
class Outcome:
    kind: str                 # success | error | release | share | cancel | survivors
    message: str = ''
    reason: str = ''          # release reason or cancel cause
    flow: object = None       # FlowResult on success


@dataclass
class DeliveryDeps:
    release_job: Callable[[int, str], bool]
    mark_unreachable: Callable[[str], None]
    upload: Callable                      # (job_id, handle, filename) -> bool
    report_pilot: Callable                # (job_id, body) -> PilotReport
    report_shots: Callable                # (job_id, body) -> PilotReport (shots-result/, FR-SD-8)
    upload_frame: Callable                # (job_id, frame, handle, filename) -> PilotReport (frames/, FR-SW-10)
    report_piece: Callable                # (job_id, body) -> PilotReport (piece-result/, FR-SW-10)


def result(*, success=False, canceled=False, stdout='', error='', released=False, render_time=None,
           after_report=None) -> RenderResult:
    return RenderResult(success=success, was_canceled=canceled, was_yielded=False, stdout=stdout, stderr='',
                        error_message=error, output_path=None, thumbnail_path=None, yield_monitor=None,
                        grace_outcome=None, render_time_seconds=render_time, released=released,
                        after_report=after_report)


class Delivery:

    def __init__(self, params, deps: DeliveryDeps, summary, after_report: Optional[Callable[[], None]],
                 cause: Optional[Callable[[], str]] = None):
        self.params, self.deps, self.summary, self.after = params, deps, summary, after_report
        self.cause = cause or (lambda: '')  # JobRun.cause: a piece's uploads stop on shutdown or cancel

    def _text(self) -> str:
        return self.summary.text()

    def failed(self, message: str, prefix: bool = True) -> RenderResult:
        if self.params.is_pilot and prefix:
            message = pilot_message(message)
        logger.error("Unreal job %s failed: %s", self.params.job_id, message)
        return result(stdout=self._text(), error=message, after_report=self.after)

    def released(self, reason: str, message: str = '') -> RenderResult:
        if message:
            logger.warning("Unreal job %s released (%s): %s", self.params.job_id, reason, message)
        self.deps.release_job(self.params.job_id, reason)
        return result(stdout=self._text(), released=True, after_report=self.after)

    def deliver(self, outcome: Outcome, integrity_ok: bool) -> RenderResult:
        kind = outcome.kind
        if kind == 'success':
            return self._success(outcome.flow) if integrity_ok else self.failed(INTEGRITY_MESSAGE)
        if kind == 'release':
            return self.released(outcome.reason, outcome.message)
        if kind == 'share':
            self.deps.mark_unreachable(outcome.message)
            return self.released('share_unavailable', outcome.message)
        if kind == 'cancel':
            if outcome.reason == 'canceled':
                return result(canceled=True, stdout=self._text(), after_report=self.after)
            if outcome.reason == 'shutdown':
                return self.released('shutdown')
            if outcome.reason == WORKER_HARDWARE_CHANGED:
                return self.released(WORKER_HARDWARE_CHANGED, HARDWARE_MESSAGE)
            logger.warning("Unreal job %s was reassigned; nothing is reported.", self.params.job_id)
            return result(released=True, stdout=self._text(), after_report=self.after)
        return self.failed(outcome.message)

    def _success(self, flow) -> RenderResult:
        if self.params.is_shots:
            return sequence_delivery.deliver_shots(self, flow)
        if self.params.is_piece:
            return sequence_delivery.deliver_piece(self, flow)
        return self._pilot(flow) if self.params.is_pilot else self._upload(flow)

    def _upload(self, flow) -> RenderResult:
        path = flow.outputs[0]
        try:
            handle = fs_safe.open_output_file(path, path.parent)
        except OSError as exc:
            logger.error("The output of job %s failed the output safety checks: %s", self.params.job_id, exc)
            return self.failed(f"Unreal output failed the output safety checks ({type(exc).__name__}); not uploaded.")
        try:
            uploaded = self.deps.upload(self.params.job_id, handle, path.name)
        except JobNoLongerAssigned:
            logger.info("Job %s is no longer assigned to this worker; output discarded.", self.params.job_id)
            return result(released=True, stdout=self._text(), after_report=self.after)
        finally:
            handle.close()
        if not uploaded:
            return self.failed(UPLOAD_MESSAGE)
        try:
            os.remove(fs_safe.long_path(path))
        except OSError as exc:
            logger.warning("Could not delete the uploaded output %s: %s", path, exc)
        return result(success=True, stdout=self._text(), render_time=flow.render_time_seconds,
                      after_report=self.after)

    def _pilot(self, flow) -> RenderResult:
        from shared.unreal.validation import overrides_sha256
        gpu = flow.card  # the job's card, found by key (pilot.check_card); every trace holds it alone (D22)
        if gpu is None:
            return self.failed("the card of the measured peak is not in the GPU memory reading.")
        if gpu.card_key is None:  # backstop (the reserver refuses a keyless pilot): this worker's fault, requeue
            return self.released('local_conflict', NO_CARD_KEY_MESSAGE)
        body = pilot.report_body(flow.metrics, gpu, prep_s=flow.prep_s, warmup_s=flow.warmup_s,
                                 render_s=flow.render_s, frames=len(flow.prep.pilot_frames),
                                 engine_build=self.params.engine_build,
                                 overrides_sha256=overrides_sha256(dict(self.params.cvar_overrides)),
                                 summary=self._text(), game_overrides=flow.prep.game_overrides,
                                 texture_streaming=flow.prep.texture_streaming)
        report = self.deps.report_pilot(self.params.job_id, body)
        if report.outcome == 'rejected':
            return self.failed(f"Pilot result rejected by the manager: {report.detail}"[:1000], prefix=False)
        if report.outcome == 'failed':
            logger.error("pilot-result/ for job %s failed after retries (%s); live reconciliation requeues it.",
                         self.params.job_id, report.detail or report.status_code)
        elif report.outcome == 'terminal':
            logger.info("pilot-result/ for job %s answered %s; nothing more is sent.", self.params.job_id,
                        report.status_code)
        else:
            logger.info("Pilot job %s measured: peak %s MB (%s).", self.params.job_id, flow.metrics.peak_mb,
                        flow.metrics.peak_source)
        return result(success=True, released=True, stdout=self._text(), after_report=self.after)
