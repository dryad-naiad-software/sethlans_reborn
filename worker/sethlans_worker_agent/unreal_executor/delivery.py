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
  ``"Pilot result rejected by the manager: <detail>"``;
* failure -> ``ERROR`` (pilot: ``"Pilot render failed: <reason>"``);
* local conflict -> ``release/`` with its reason; share failure ->
  ``release/`` with ``share_unavailable`` and ``mark_unreachable``;
* ``canceled`` -> ``CANCELED``; ``shutdown`` -> ``release/`` with
  ``shutdown``; ``reassigned`` -> nothing reported.
"""
import logging
import os
from dataclasses import dataclass
from typing import Callable, Optional

from sethlans_worker_agent.api_conflict import JobNoLongerAssigned
from sethlans_worker_agent.executors.base import RenderResult
from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor import pilot
from sethlans_worker_agent.unreal_executor.outcomes import pilot_message

logger = logging.getLogger(__name__)

INTEGRITY_MESSAGE = "The pristine project cache failed its integrity check after the render; output discarded."
UPLOAD_MESSAGE = "Unreal output upload failed."


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


def result(*, success=False, canceled=False, stdout='', error='', released=False, render_time=None,
           after_report=None) -> RenderResult:
    return RenderResult(success=success, was_canceled=canceled, was_yielded=False, stdout=stdout, stderr='',
                        error_message=error, output_path=None, thumbnail_path=None, yield_monitor=None,
                        grace_outcome=None, render_time_seconds=render_time, released=released,
                        after_report=after_report)


class Delivery:

    def __init__(self, params, deps: DeliveryDeps, summary, after_report: Optional[Callable[[], None]]):
        self.params, self.deps, self.summary, self.after = params, deps, summary, after_report

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
            if not integrity_ok:
                return self.failed(INTEGRITY_MESSAGE)
            return self._pilot(outcome.flow) if self.params.is_pilot else self._upload(outcome.flow)
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
            logger.warning("Unreal job %s was reassigned; nothing is reported.", self.params.job_id)
            return result(released=True, stdout=self._text(), after_report=self.after)
        return self.failed(outcome.message)

    def _upload(self, flow) -> RenderResult:
        path = flow.outputs[0]
        try:
            handle = fs_safe.open_output_file(path, path.parent)
        except OSError as exc:
            return self.failed(f"Unreal output failed the output safety checks ({exc}); not uploaded.")
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
        gpu = pilot.gpu_of(flow.gate_reading, flow.metrics.gpu_index)
        if gpu is None:
            return self.failed("the GPU of the measured peak is not in the nvidia-smi reading.")
        body = pilot.report_body(flow.metrics, gpu, prep_s=flow.prep_s, warmup_s=flow.warmup_s,
                                 render_s=flow.render_s, frames=len(flow.prep.pilot_frames),
                                 engine_build=self.params.engine_build,
                                 overrides_sha256=overrides_sha256(dict(self.params.cvar_overrides)),
                                 summary=self._text())
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
