# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Success delivery of sequence rendering's job kinds (FR-SD-8, FR-SW-10,
FR-SS-4, FR-SA-6). Failure, release, cancel and share outcomes
stay in ``delivery``. The manager sets these jobs DONE itself (a worker
PATCH to DONE is refused, D12), so the lifecycle reports nothing more
(``released=True``), as for a pilot.

``shots``: the checked discovery goes to ``shots-result/``.

A **piece**: every kept frame goes to ``frames/`` in frame order, each
opened with ``fs_safe.open_output_file`` and uploaded from that handle (the
bytes sent are the bytes checked), then deleted; then the timings go to
``piece-result/``. Before each call the job's cause is checked
(``Delivery.cause``): a shutdown, cancel or reassignment stops the uploads
and is delivered as that outcome (a shutdown releases the job), so a long
EXR piece never holds the job thread past the shutdown budget. A lost card
(``worker_hardware_changed``) does not stop them: the frames are on disk.

An **audio job**: the run's ``.wav`` is opened with
``fs_safe.open_output_file``, checked again from that handle
(``wav_check.check_handle``: the bytes sent are the bytes checked) and
streamed to ``sound-result/`` with the ``offset`` object, then deleted.

For every call:

* 200 -> on; 409/404 -> stop, nothing more is sent (the job is no longer
  this worker's);
* 5xx or a network error after the retries (or an auth failure) -> logged,
  the job is left ``RENDERING`` and live reconciliation requeues it;
* another 4xx -> ``ERROR`` with ``"<what> rejected by the manager:
  <detail>"``.
"""
import logging
import os

from sethlans_worker_agent.project_archive import fs_safe
from sethlans_worker_agent.unreal_executor import wav_check
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure

logger = logging.getLogger(__name__)

REJECTED = "Shot discovery result rejected by the manager: {detail}"
FRAME_REJECTED = "Frame {frame} rejected by the manager: {detail}"
PIECE_REJECTED = "Piece result rejected by the manager: {detail}"
SOUND_REJECTED = "Sound result rejected by the manager: {detail}"
MAX_WARNINGS, MAX_WARNING_CHARS = 32, 512


def _done(delivery):
    from sethlans_worker_agent.unreal_executor.delivery import result
    return result(success=True, released=True, stdout=delivery.summary.text(), after_report=delivery.after)


def _interrupted(delivery):
    """The job's result when a shutdown, cancel or reassignment arrived meanwhile; ``None`` to go on."""
    from sethlans_worker_agent.blender_job_stop import WORKER_HARDWARE_CHANGED
    from sethlans_worker_agent.unreal_executor.delivery import Outcome
    cause = delivery.cause()
    if not cause or cause == WORKER_HARDWARE_CHANGED:
        return None
    logger.warning("Uploads of job %s stopped (%s).", delivery.params.job_id, cause)
    return delivery.deliver(Outcome('cancel', reason=cause), True)


def _stopped(delivery, report, endpoint: str, rejected: str):
    """``None`` to go on; else the job's result for a terminal, failed or rejected answer."""
    job_id = delivery.params.job_id
    if report.outcome == 'done':
        return None
    if report.outcome == 'rejected':
        return delivery.failed(rejected.format(detail=report.detail)[:1000], prefix=False)
    if report.outcome == 'failed':
        logger.error("%s for job %s failed after retries (%s); live reconciliation requeues it.",
                     endpoint, job_id, report.detail or report.status_code)
    else:
        logger.info("%s for job %s answered %s; nothing more is sent.", endpoint, job_id, report.status_code)
    return _done(delivery)


def deliver_shots(delivery, flow):
    """``delivery``: the job's ``Delivery``; ``flow``: ``sequence_flow.ShotsResult``."""
    job_id, discovery = delivery.params.job_id, flow.discovery
    report = delivery.deps.report_shots(job_id, discovery.body())
    stopped = _stopped(delivery, report, 'shots-result/', REJECTED)
    if stopped is not None:
        return stopped
    logger.info("Shots job %s found %d shots (%s).", job_id, len(discovery.shots), discovery.shot_source)
    return _done(delivery)


def piece_body(flow, summary) -> dict:
    """The FR-SW-10 ``piece-result/`` body from a ``FlowResult`` with its ``PieceOutput``."""
    piece, timing = flow.piece, flow.piece.timing
    card = piece.card
    gpu = None
    if card is not None and card.card_key:
        gpu = {'card_key': card.card_key, 'name': card.name, 'total_mb': card.total_mb}
    peak = piece.vram_peak_mb
    return {'render_s': round(min(float(flow.render_s), 259_200.0), 3), 'startup_s': timing.startup_s,
            'lead_in_s': timing.lead_in_s, 'vram_peak_mb': max(int(peak), 0) if peak is not None else None,
            'gpu': gpu, 'shots': [dict(row) for row in timing.shots],
            'warnings': [text[:MAX_WARNING_CHARS] for text in list(summary.warnings())[:MAX_WARNINGS]],
            'summary': summary.text()}


def _upload_frames(delivery, flow):
    """Each kept frame to ``frames/``; ``None`` when all went, else the job's result."""
    job_id = delivery.params.job_id
    for frame, path in zip(flow.piece.frames, flow.outputs):
        interrupted = _interrupted(delivery)
        if interrupted is not None:
            return interrupted
        try:
            handle = fs_safe.open_output_file(path, path.parent)
        except OSError as exc:
            logger.error("Frame %s of job %s failed the output safety checks: %s", frame, job_id, exc)
            return delivery.failed(f"Frame {frame} failed the output safety checks ({type(exc).__name__}); "
                                   "not uploaded.")
        try:
            report = delivery.deps.upload_frame(job_id, frame, handle, path.name)
        finally:
            handle.close()
        stopped = _stopped(delivery, report, f'frames/ {frame}', FRAME_REJECTED.replace('{frame}', str(frame)))
        if stopped is not None:
            return stopped
        try:
            os.remove(fs_safe.long_path(path))
        except OSError as exc:
            logger.warning("Could not delete the uploaded frame %s: %s", path, exc)
    return None


def deliver_piece(delivery, flow):
    """FR-SW-10: the kept frames, then ``piece-result/``; ``flow``: a ``FlowResult`` with ``piece`` set."""
    stopped = _upload_frames(delivery, flow)
    if stopped is None:
        stopped = _interrupted(delivery)
    if stopped is not None:
        return stopped
    report = delivery.deps.report_piece(delivery.params.job_id, piece_body(flow, delivery.summary))
    stopped = _stopped(delivery, report, 'piece-result/', PIECE_REJECTED)
    if stopped is not None:
        return stopped
    logger.info("Piece %s DONE: %d frames uploaded.", delivery.params.job_id, len(flow.outputs))
    return _done(delivery)


def _upload_cause(delivery) -> str:
    """The job's cause, without a lost card: the ``.wav`` is on disk, so it still goes up (as a piece's frames)."""
    from sethlans_worker_agent.blender_job_stop import WORKER_HARDWARE_CHANGED
    cause = delivery.cause()
    return '' if cause == WORKER_HARDWARE_CHANGED else cause


def deliver_sound(delivery, flow):
    """FR-SA-6: the run's ``.wav`` and its offset; ``flow``: a ``FlowResult`` with ``sound`` set."""
    sound, job_id = flow.sound, delivery.params.job_id
    stopped = _interrupted(delivery)
    if stopped is not None:
        return stopped
    try:
        handle = fs_safe.open_output_file(sound.path, sound.path.parent)
    except OSError as exc:
        logger.error("The sound of job %s failed the output safety checks: %s", job_id, exc)
        return delivery.failed(f"The sound file failed the output safety checks ({type(exc).__name__}); not uploaded.")
    try:
        wav_check.check_handle(handle, sound.frames, sound.rate_fraction, sound.latency_skipped)
        report = delivery.deps.upload_sound(job_id, handle, sound.offset, sound.latency_skipped, flow.render_s,
                                            delivery.summary.text(), cause=lambda: _upload_cause(delivery))
    except JobFailure as exc:
        return delivery.failed(exc.message)
    finally:
        handle.close()
    if report.outcome == 'stopped':  # a shutdown, cancel or reassignment arrived during the upload
        return _interrupted(delivery) or _done(delivery)
    stopped = _stopped(delivery, report, 'sound-result/', SOUND_REJECTED)
    if stopped is not None:
        return stopped
    try:
        os.remove(fs_safe.long_path(sound.path))
    except OSError as exc:
        logger.warning("Could not delete the uploaded sound %s: %s", sound.path, exc)
    logger.info("Audio job %s DONE: sound offset %s (%s).", job_id, sound.offset['offset_samples'],
                sound.offset['status'])
    return _done(delivery)
