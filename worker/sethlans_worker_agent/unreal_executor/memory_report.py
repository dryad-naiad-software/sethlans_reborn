# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A render or audio job's memory report (Unreal per-machine memory test D3,
D6, D9, FR-MT-S-3, FR-MT-S-4).

``send(job_run)`` runs in ``job_run.JobRun.run`` after exit confirmation and
``finish``, **before** the delivery (the completion call: ``piece-result/``,
``sound-result/``, the upload, the ERROR or CANCELED report, ``release/``),
so it covers every end path: success, failure, cancel, release, survivors.
It sends once per job when the job is a ``render`` (not a pilot: its
``pilot-result/`` carries the figures) or ``audio`` job and at least one
render launch ran (``PhaseRunner.runs`` holds a ``render`` launch). The body
(strict, the manager refuses any other key)::

    {"measurement_id": int,          # the result the gate used (GateResult.result)
     "card_kind": "vvvv:dddd:rrrr",  # the gated card's kind (GateResult.card_kind)
     "card_key": "pci:...",          # the held card the job rendered on (reservation.card_key)
     "driver_version": str | null,   # the render launch's internal: driver; Unknown -> null (D3)
     "spilled": bool | null,         # D9 over the render launches (spill.outcome)
     "spill_cause": "self" | "contention" | null,  # null exactly when spilled is not true
     "shared_peak_mb": int | null,   # the job's own shared peak on its card, 0..MAX_PEAK_MB
     "job_peak_mb": int | null}      # the job's own dedicated peak on its card, 1..MAX_PEAK_MB

``job_peak_mb`` is the job's own Job Object processes' dedicated peak on its
card over the whole of every render launch, start-up spike included
(``job_memory.job_peak_mb``; the pilot's result peak includes its start-up
too), never the card's rise over its baseline: the rise counts other
programs, and D6's feedback would turn a result ``does_not_fit`` for their
memory. 0 (nothing attributed to the job) is sent as null; other figures
outside their range are sent as null (logged).

The driver is read from the render launch's log (``pilot_flow.driver``,
which also sets ``driver_version`` and ``driver_source`` in the summary);
when it differs from the result's own driver (both known) the summary gets
``driver_changed=1`` and an INFO line: the manager re-tests the machine (D3).

Responses (``api_extras.report_memory``, the standard report retries; one
attempt only on a shutdown, whose budget the retries would exceed): 200 ->
``memory_report=sent``; 404/409 -> ``refused`` (the job is no longer this
worker's, e.g. canceled; logged); a failure after the retries, a refused body
(other 4xx), a report that cannot be built or no report call -> ERROR and
``memory_report=failed``. The completion goes ahead in every case. Nothing
raises out of ``send``.
"""
import logging
from typing import Optional

from sethlans_worker_agent.cards.identity import key_address
from sethlans_worker_agent.unreal_executor import job_memory, pilot_flow, spill
from shared.unreal.card_kind import is_card_kind
from shared.unreal.vram_gate import MAX_PEAK_MB

logger = logging.getLogger(__name__)

SENT, REFUSED, FAILED = 'sent', 'refused', 'failed'
QUIET_CAUSES = ('canceled', 'reassigned')  # a refused report is expected then


class ReportError(ValueError):
    """The report cannot be built (no result, kind or card key from the gate)."""


def due(params, runner) -> bool:
    """A render (not pilot) or audio job with at least one render launch."""
    if runner is None or not (params.kind == 'render' or params.is_audio):
        return False
    return any(run.phase == 'render' for run in getattr(runner, 'runs', ()))


def _in_range(name: str, value: Optional[int], low: int) -> Optional[int]:
    if value is None or low <= value <= MAX_PEAK_MB:
        return value
    logger.warning("The memory report's %s (%s MB) is outside [%s, %s]; it is sent as null.", name, value, low,
                   MAX_PEAK_MB)
    return None


def _driver(job_run, runs) -> Optional[str]:
    renders = [run.log_path for run in runs if run.phase == 'render']
    if job_run.flow is None:  # cannot happen once a render launch ran (the flow launched it)
        raise ReportError("no flow for the driver version")
    return pilot_flow.driver(job_run.flow, renders[-1] if renders else None)


def _note_driver(summary, result, driver: Optional[str], job_id) -> None:
    """D3: a known launch driver that differs from the result's known driver; the manager re-tests."""
    changed = driver is not None and result.driver_version is not None and driver != result.driver_version
    summary.set('driver_changed', 1 if changed else 0)
    if changed:
        logger.info("Unreal job %s ran with driver %s; its memory result was measured with %s, so the manager "
                    "tests this machine again.", job_id, driver, result.driver_version)


def body(job_run) -> dict:
    """The FR-MT-S-3 body; ``ReportError`` when the gate gave no result, kind or card key."""
    gate, runs = job_run.reservation.gate, job_run.runner.runs
    result, kind = getattr(gate, 'result', None), getattr(gate, 'card_kind', None)
    card_key = job_run.reservation.card_key
    if result is None:
        raise ReportError("the gate chose no memory result")
    if not is_card_kind(kind):
        raise ReportError(f"the gated card has no card kind ({kind!r})")
    if key_address(card_key) is None:
        raise ReportError(f"the held card has no PCI card key ({card_key!r})")
    driver = _driver(job_run, runs)
    _note_driver(job_run.summary, result, driver, job_run.params.job_id)
    outcome = spill.outcome(runs)
    return {'measurement_id': result.measurement_id, 'card_kind': kind, 'card_key': card_key,
            'driver_version': driver, 'spilled': outcome.spilled,
            'spill_cause': outcome.cause if outcome.spilled is True else None,
            'shared_peak_mb': _in_range('shared_peak_mb', outcome.shared_peak_mb, 0),
            'job_peak_mb': _in_range('job_peak_mb', job_memory.job_peak_mb(runs) or None, 1)}


def _deliver(job_run, report_body: dict) -> str:
    job_id, cause = job_run.params.job_id, job_run.cause()
    call = job_run.deps.delivery.report_memory
    if call is None:
        logger.error("Unreal job %s: no memory-report call is wired; the report is not sent.", job_id)
        return FAILED
    report = call(job_id, report_body, attempts=1) if cause == 'shutdown' else call(job_id, report_body)
    if report.outcome == 'done':
        logger.info("Unreal job %s memory report sent: %s", job_id, report_body)
        return SENT
    if report.outcome == 'terminal':
        log = logger.info if cause in QUIET_CAUSES else logger.warning
        log("memory-report/ for job %s answered %s (%s); not sent again.", job_id, report.status_code, report.detail)
        return REFUSED
    logger.error("memory-report/ for job %s %s (%s); the job's completion goes ahead without it.", job_id,
                 'was refused' if report.outcome == 'rejected' else 'failed after retries',
                 report.detail or report.status_code)
    return FAILED


def send(job_run) -> None:
    """FR-MT-S-4: the report once, before the completion call, on every end path; never raises."""
    params = job_run.params
    if not due(params, getattr(job_run, 'runner', None)):
        return
    try:
        state = _deliver(job_run, body(job_run))
    except ReportError as exc:
        logger.error("Unreal job %s: the memory report cannot be built (%s); it is not sent.", params.job_id, exc)
        state = FAILED
    except Exception:  # noqa: BLE001 - a bug here must not stop the job's completion
        logger.exception("Unreal job %s: the memory report failed unexpectedly.", params.job_id)
        state = FAILED
    job_run.summary.set('memory_report', state)
