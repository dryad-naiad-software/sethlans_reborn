# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Phase outcome checks (Unreal foundation FR-UE-8, FR-UE-9, FR-UE-10,
FR-UE-12, FR-UE-13, FR-UE-15; S1, S3).

* ``check_prep`` / ``check_warmup``: the phase counts as successful on exit
  0 (warm-up: also its ``[Warm] done, quitting`` line) or after a forced
  quit that followed the success marker (``exited_after_grace``).
* ``check_render``: the whole log is scanned to EOF after exit
  confirmation (cancellations can follow ``RequestExitWithStatus``).
  ``retry`` is set for ``compilation_cancelled > 0`` or a forced quit
  (FR-UE-10); otherwise the FR-UE-13 rules apply: exit 0 and ``exited``,
  ``mrq_executor_finished == expected_jobs``, ``exit_status_zero >= 1`` and
  no fatal hit.
"""
from dataclasses import dataclass
from typing import Optional

from sethlans_worker_agent.unreal_executor import log_validation
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure

RETRY_CANCELLED = ("Unreal render had {n} cancelled kernel compilations after warm-up retry "
                   "(see render.2.log).")
RETRY_QUIT = ("Unreal did not exit within {grace} s after the render finished, twice; cancelled compilations "
              "cannot be ruled out (see render.2.log).")


def _failure_text(phase: str, outcome) -> str:
    if outcome.reason == 'fatal':
        if outcome.fatal_rule == log_validation.LOG_FLOOD:
            return outcome.detail or f"Unreal {phase} log flood."
        return f"Unreal {phase} hit a fatal log line ({outcome.fatal_rule}): {outcome.detail[:300]}"
    if outcome.reason == 'timeout':
        return f"Unreal {phase} timed out after {int(outcome.wall_s)} s."
    if outcome.reason == 'hang':
        return f"Unreal {phase} hung ({outcome.detail})."
    return f"Unreal {phase} failed (exit code {outcome.exit_code}, {outcome.reason})."


def phase_ok(outcome) -> bool:
    """Exit 0, or a forced quit after the success marker (prep, warm-up)."""
    if outcome.reason == 'exited_after_grace':
        return True
    return outcome.reason == 'exited' and outcome.exit_code == 0


def check_prep(outcome) -> None:
    """Raise ``JobFailure`` unless the prep exited successfully (the result is checked separately)."""
    if outcome.reason == 'exited' and outcome.exit_code not in (0, None):
        return  # the commandlet returns -1 when the script raised: the result file carries the error
    if not phase_ok(outcome):
        raise JobFailure(_failure_text('prep pass', outcome))


def check_warmup(outcome) -> None:
    if outcome.reason == 'exited' and outcome.exit_code == 0 and not outcome.success_marker_seen:
        raise JobFailure("Unreal warm-up exited without its '[Warm] done, quitting' line.")
    if not phase_ok(outcome):
        raise JobFailure(_failure_text('warm-up', outcome))


@dataclass
class RenderCheck:
    scan: log_validation.LogScan
    retry: str = ''        # '' | 'cancelled' | 'quit_forced'
    cancelled: int = 0


def _scan(outcome, log_path, max_bytes: Optional[int]) -> log_validation.LogScan:
    if outcome.reason not in ('exited', 'exited_after_grace'):
        raise JobFailure(_failure_text('render', outcome))
    try:
        scan = log_validation.scan_log(log_path, max_bytes=max_bytes)
    except OSError as exc:
        raise JobFailure(f"Unreal render log could not be read ({exc}).") from exc
    if scan.oversized:
        raise JobFailure("Unreal render log grew past the size limit (log flood).")
    fatal = scan.fatal_hits()
    if fatal:
        raise JobFailure(f"Unreal render hit a fatal log line ({fatal[0]}).")
    return scan


def check_render(outcome, log_path, expected_jobs: int, max_bytes: Optional[int] = None) -> RenderCheck:
    """FR-UE-10/13 on one render attempt; raises ``JobFailure`` for a non-retryable failure."""
    scan = _scan(outcome, log_path, max_bytes)
    check = RenderCheck(scan=scan, cancelled=scan.count('compilation_cancelled'))
    if check.cancelled:
        check.retry = 'cancelled'
        return check
    if outcome.reason == 'exited_after_grace':
        check.retry = 'quit_forced'
        return check
    if outcome.exit_code != 0:
        raise JobFailure(f"Unreal render failed (exit code {outcome.exit_code}).")
    jobs = scan.executor_jobs()
    if jobs != expected_jobs:
        raise JobFailure(f"Movie Render Queue finished {jobs} jobs; expected {expected_jobs}.")
    if scan.count('exit_status_zero') < 1:
        raise JobFailure("Unreal render did not request a clean exit (RequestExitWithStatus(0, 0)).")
    return check


def retry_failure(check: RenderCheck, quit_grace_s: float) -> JobFailure:
    """The ERROR text when the FR-UE-10 retry needed a retry again."""
    if check.retry == 'cancelled':
        return JobFailure(RETRY_CANCELLED.format(n=check.cancelled))
    return JobFailure(RETRY_QUIT.format(grace=int(quit_grace_s)))
