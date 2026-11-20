# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Success delivery of sequence rendering's job kinds (FR-SD-8; pieces and
sound come later). Failure, release, cancel and share outcomes stay in
``delivery``.

``shots``: the checked discovery goes to ``shots-result/``, where the
manager sets the job DONE (a worker PATCH to DONE is refused, D12), so the
lifecycle reports nothing more (``released=True``), as for a pilot:

* 200 -> done; 409/404 -> terminal (the job is no longer this worker's);
* 5xx or a network error after the retries -> logged, the job is left
  ``RENDERING`` and live reconciliation requeues it;
* another 4xx -> ``ERROR`` with ``"Shot discovery result rejected by the
  manager: <detail>"``.
"""
import logging

logger = logging.getLogger(__name__)

REJECTED = "Shot discovery result rejected by the manager: {detail}"


def deliver_shots(delivery, flow):
    """``delivery``: the job's ``Delivery``; ``flow``: ``sequence_flow.ShotsResult``."""
    from sethlans_worker_agent.unreal_executor.delivery import result
    job_id, discovery = delivery.params.job_id, flow.discovery
    report = delivery.deps.report_shots(job_id, discovery.body())
    if report.outcome == 'rejected':
        return delivery.failed(REJECTED.format(detail=report.detail)[:1000], prefix=False)
    if report.outcome == 'failed':
        logger.error("shots-result/ for job %s failed after retries (%s); live reconciliation requeues it.",
                     job_id, report.detail or report.status_code)
    elif report.outcome == 'terminal':
        logger.info("shots-result/ for job %s answered %s; nothing more is sent.", job_id, report.status_code)
    else:
        logger.info("Shots job %s found %d shots (%s).", job_id, len(discovery.shots), discovery.shot_source)
    return result(success=True, released=True, stdout=delivery.summary.text(), after_report=delivery.after)
