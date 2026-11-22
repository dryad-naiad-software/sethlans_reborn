# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A pilot's measurement step in ``job_flow.UeFlow`` (Unreal foundation
FR-UE-25; GPU memory reader FR-GU-4; per-machine memory test D3, D6,
FR-MT-V-1..3).

``collect(flow, result, prep, run, before)`` runs after a pilot's render
succeeded: UE's chosen adapter name (summary), the rule that every warm-up
and render log names the chosen adapter (``require_adapter_lines``), the
card it rendered on (``pilot.check_card``), the CSV capture, the metrics and
D6's verdict (``pilot.measure``), the RAM figures, and the driver version
(``driver``).

``after_failure(flow, prep, failure)`` is D6's second case: a pilot whose
warm-up or render launch failed (``JobFailure``) **after a saturated sample**
(``pilot.saturated`` over the launches so far) reports ``does_not_fit``
with the card figures sampled until the failure (no CSV capture: it may be
cut short), and ends DONE like any measured pilot. Without a saturated
sample, for a failure that did not come from a launch's run (an
``IsolationFailure``: the Zen/ini guard before a launch, the Zen log check),
or when the figures cannot be tied to the card (no adapter line, no reading
of the card, no sample, an implausible peak), the original failure is raised
again, so the job fails as before.

**Other programs (owner decision Q10, D9's contention rule for the pilot).**
``check_others(flow, runs)``: per sample of every sampled launch (prep,
warm-up, render; the whole launch, start-up included), other programs' use
of the card is the card's ``used`` minus the launch's own Job Object members'
dedicated usage on it (``VramTrace.max_others``, from the reading's per-pid
map); its reference is the same figure in the launch's pre-launch reading
(the runner's ``vram_baseline``; no member runs yet, so it is the card's
``used`` then). When it rose by more than ``unreal.spill_margin_mb`` the
pilot records **no verdict**: ``LocalConflict('local_conflict')`` releases
it (no pilot report; it runs again later), with a WARNING and a summary
warning. Checked before the measurement on success, and in
``after_failure`` before a saturated failure becomes ``does_not_fit`` (a
card filled by another program is not the pilot's figure). A launch without
attributed samples (no per-pid map: not Windows, or a reader without it)
or without a pre-launch reading is not checked; ``pilot_others_rise_mb`` is
then empty in the summary.

``driver(flow, render_log)``: the chosen adapter's ``internal:`` driver
version (``driver_log.launch_driver``) from the render launch's log; the
warm-up log is read only when the render log has no adapter lines, and only
a warm-up launched on the render's card (by card key address: every launch
of a flow is on the first launch's card, ``UeFlow._adapter_ctx``, so this
holds by construction and is checked here once more). ``None`` when unknown;
the summary says ``driver_version=unknown`` (``driver_log`` logs the WARNING).
"""
import logging
from typing import Optional, Tuple

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.cards.identity import key_address
from sethlans_worker_agent.unreal_executor import driver_log, job_memory, output, pilot, pilot_csv
from sethlans_worker_agent.unreal_executor.outcomes import IsolationFailure, JobFailure, LocalConflict

logger = logging.getLogger(__name__)

NO_ADAPTER_LINE_MESSAGE = ("the Unreal log did not say which graphics card it rendered on, so the measured peak "
                           "cannot be tied to the job's card; the job goes back to the queue.")
UNKNOWN_DRIVER = 'unknown'
OTHERS_MESSAGE = ("other programs' use of the pilot's graphics card rose by {rise} MB during the {phase} (more than "
                  "the {margin} MB margin), so the measurement is not the pilot's own; it runs again later.")


def others_rise(runs) -> Optional[Tuple[int, str]]:
    """Q10: ``(largest rise of other programs' use of the card over its pre-launch figure, phase)``; None unknown."""
    best = None
    for run in runs:
        others = getattr(run.outcome.vram, 'max_others', {})
        for index, value in others.items():
            if run.pre is not None and index in run.pre:
                rise = max(0, value - run.pre[index])
                if best is None or rise > best[0]:
                    best = (rise, run.phase)
    return best


def check_others(flow, runs) -> None:
    """Q10: no verdict when other programs' use of the card rose past the spill margin; release the pilot."""
    margin = config_unreal.SPILL_MARGIN_MB  # read at call time (FR-MT-S-2's key, shared with chunk 6's D9)
    rise = others_rise(runs)
    flow.summary.set('pilot_others_rise_mb', '' if rise is None else rise[0])
    if rise is None or rise[0] <= margin:
        return
    message = OTHERS_MESSAGE.format(rise=rise[0], phase=rise[1].replace('warmup', 'warm-up'), margin=margin)
    logger.warning("Pilot job %s records no verdict: %s", flow.params.job_id, message)
    flow.summary.warn(f"pilot_others_rose: {message}"[:300])
    raise LocalConflict('local_conflict', message)


def require_adapter_lines(runs, adapter: str) -> None:
    """A pilot's peak is tied to its card only when every warm-up and render log says which card UE chose."""
    if not adapter or any(r.outcome.adapter_chosen is None for r in runs if r.phase in ('warmup', 'render')):
        raise LocalConflict('local_conflict', NO_ADAPTER_LINE_MESSAGE)


def _same_card_warmup_log(flow):
    """The last warm-up log launched on the render's card (``flow.warmup_logs``: ``(card key, log)``)."""
    card = key_address(flow.choice.key) if flow.choice is not None else None
    for key, log_path in reversed(getattr(flow, 'warmup_logs', ())):
        if card is not None and key_address(key) == card:
            return log_path
    return None


def driver(flow, render_log) -> Optional[str]:
    """FR-MT-V-1: the render launch's driver version (``None`` unknown); sets the summary values."""
    warmup_log = _same_card_warmup_log(flow)
    if render_log is not None:
        reading = driver_log.launch_driver(render_log, warmup_log)
    elif warmup_log is not None:  # a pilot whose warm-up failed: no render launch (after_failure)
        reading = driver_log.launch_driver(None, warmup_log)
    else:
        logger.warning("The graphics driver version is unknown (no warm-up or render log); it is reported as null.")
        reading = driver_log.DriverReading(None, None)
    flow.summary.update({'driver_version': reading.version or UNKNOWN_DRIVER, 'driver_source': reading.source or ''})
    return reading.version


def _adapter(render_log, runs) -> str:
    """UE's name for its chosen adapter: the render log's, else (a failed render) the last warm-up log's."""
    logs = ([render_log] if render_log is not None else []) + [r.log_path for r in reversed(runs) if r.phase == 'warmup']
    for log_path in logs:
        name = pilot.chosen_adapter(log_path)
        if name:
            return name
    return ''


def _measure(flow, result, prep, render_log, csv_peak: Optional[int]):
    """Metrics, D6 verdict, RAM figures, driver and summary; ``result.card`` is already the checked card."""
    result.metrics = pilot.measure(flow.runner.runs, csv_peak)
    result.ram, result.ram_process_commit_peak_mb = job_memory.pilot_ram(flow.runner.runs)
    result.driver_version = driver(flow, render_log)
    flow.summary.update(pilot.summary_values(result.metrics, list(prep.pilot_frames), result.adapter, flow.cvars_sha))
    return result


def collect(flow, result, prep, run, before):
    """A measured pilot after a successful render: outputs, card, CSV, metrics, verdict, RAM, driver."""
    result.outputs = output.collect_pilot(flow.job.output, flow.params.job_id, prep.pilot_frames)
    result.adapter = pilot.chosen_adapter(run.log_path) or ''  # UE's name for it, summary only
    require_adapter_lines(flow.runner.runs, result.adapter)
    result.card = pilot.check_card(flow.choice, flow.reading)
    check_others(flow, flow.runner.runs)  # Q10: no verdict when other programs filled the card
    csv_peak = pilot_csv.collect(run.log_path, flow.config.profile, before, flow.job.logs)
    return _measure(flow, result, prep, run.log_path, csv_peak)


def after_failure(flow, prep, failure: JobFailure, result):
    """D6: a launch that failed after a saturated sample is a ``does_not_fit`` measurement; else re-raise.

    An ``IsolationFailure`` (no launch failed: the Zen/ini guard, or the Zen log check) is always re-raised;
    other programs' rise past the margin (Q10) releases the pilot instead (``check_others``).
    """
    runs = flow.runner.runs
    if not flow.params.is_pilot or isinstance(failure, IsolationFailure) or not pilot.saturated(runs):
        raise failure
    check_others(flow, runs)  # Q10: a card filled by other programs is released, never does_not_fit
    renders = [run.log_path for run in runs if run.phase == 'render']
    render_log = renders[-1] if renders else None
    try:
        result.adapter = _adapter(render_log, runs)
        require_adapter_lines(runs, result.adapter)  # the failed launch too: its samples must be the card's
        result.card = pilot.check_card(flow.choice, flow.reading)
        measured = _measure(flow, result, prep, render_log, None)
    except (JobFailure, LocalConflict) as exc:
        logger.warning("Pilot job %s failed after a saturated sample, but its figures cannot be reported (%s).",
                       flow.params.job_id, exc)
        raise failure from None
    logger.warning("Pilot job %s failed after its card was full (%s); reported as does not fit, lower bound %s MB.",
                   flow.params.job_id, failure, measured.metrics.lower_bound_mb)
    flow.summary.warn(f"pilot_failed_saturated: {failure}"[:300])
    return measured
