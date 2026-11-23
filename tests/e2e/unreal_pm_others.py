# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
A deliberate "other program" during this machine's AC-E7 pilot (Part B,
opt-in ``SETHLANS_UNREAL_E2E_PM_OTHERS_MB``; owner decision Q10, band 2).

The worker compares other programs' use of the pilot's card in every sample
of a launch with that launch's **pre-launch** reading. A program opened
between launches lands in the next launch's pre-launch reading and shows no
rise, so the holder (``gpu_memory_holder``, its own process) starts
**inside** a launch:

- **Which launch: the pilot's render launch.** It is the pilot's last
  launch, so a holder still there at its end is only in its post-exit
  reading, and the measurement's baseline is ``min(pre, post)``: never a
  later launch's pre-launch reading (that, with the holder gone during the
  launch, would read as memory freed under the baseline and release the
  pilot). It runs on every pilot, warm caches or not (a warm warm-up can be
  short or skipped), and is the launch D6 is about. Its UE start-up alone
  takes well over the trigger delay.
- **Trigger.** ``OthersHolder.poll`` runs on every poll round of the pilot
  waits (``_sample``, about every 2 s). It looks for a running Unreal
  process whose ``-abslog=`` is a job folder's ``render.<n>.log``
  (``render_launch``) and whose job is this machine's pilot (its
  ``target_worker``). The pieces are paused, so the pilot is the only Unreal
  job here. The process exists only after the launch's pre-launch reading
  (taken before the start), so the holder lands inside the launch. Its card
  is the ticked card whose DXGI index is the launch's ``-graphicsadapter``,
  passed to the holder by card key (the holder matches it by PCI address).
- **Timing.** The holder holds ``HOLD_SECONDS`` and watches the UE process
  and this test process: it frees as soon as either ends, so it never
  outlives the launch. With 1 s GPU samples that is many samples of the
  launch with the holder in them.

``check_pilot`` asserts band 2 on that pilot: DONE on the attempt the holder
ran in (a band 3 release runs the pilot again under a new attempt), no
``records no verdict`` line for it in the worker log, ``pilot_others_rise_mb``
over the margin (``pilot_margin_mb``: the worker default, asserted not
overridden), ``check_others``'s band 2 INFO line for it in the worker log
(``pilot_others_added_mb`` alone proves nothing: every summary has it), the peak equal to the
larger of the card figure and the CSV peak, the pilot's record ``fits`` with
that peak and no lower bound, and the machine row's peak (pilot and job
peaks) at least that. ``pilot_others_added_mb`` is recorded, not asserted: it is
legitimately 0 when the holder was there at the card's peak sample.
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from tests.e2e import unreal_pm_records as rec
from tests.e2e.unreal_workflow_harness import parse_summary

logger = logging.getLogger(__name__)

HOLDER = Path(__file__).with_name("gpu_memory_holder.py")
HOLD_SECONDS = 30
MARGIN_ENV = "SETHLANS_UNREAL_PILOT_OTHERS_MARGIN_MB"
STOP_WAIT_S = 30
STATUS_READ_TRIES = 5
# pilot_flow.check_others's band 2 INFO line ("Pilot job %s: %s" with OTHERS_ADDED_MESSAGE); band 1 logs nothing.
BAND2_LINE = ("Pilot job {job}: other programs' use of the pilot's graphics card rose by {rise} MB during the ")
BAND2_TAIL = "while the card stayed clear of full"
_EDITOR_PREFIX = "unrealeditor"
_RENDER_LOG = re.compile(r"[\\/]j[\\/](\d+)-([0-9a-f]+)[\\/]logs[\\/]render\.\d+\.log$", re.IGNORECASE)
_ADAPTER = re.compile(r"-graphicsadapter=(\d+)$", re.IGNORECASE)


def render_launch(cmdline):
    """``(job id, attempt, DXGI index or None)`` of a render launch's command line, else ``None``."""
    job = index = None
    for part in cmdline or ():
        text = (part or "").strip('"')
        if text.lower().startswith("-abslog="):
            match = _RENDER_LOG.search(text)
            job = (int(match.group(1)), match.group(2)) if match else None
        match = _ADAPTER.fullmatch(text)
        if match:
            index = int(match.group(1))
    return None if job is None else (job[0], job[1], index)


def render_launches():
    """``[(pid, job id, attempt, DXGI index)]`` of the Unreal render launches running now."""
    import psutil
    found = []
    for proc in psutil.process_iter(["name", "pid"]):
        if not (proc.info.get("name") or "").lower().startswith(_EDITOR_PREFIX):
            continue
        try:
            launch = render_launch(proc.cmdline())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if launch is not None:
            found.append((proc.info["pid"], *launch))
    return found


def pilot_margin_mb(environ=None, worker_env=None):
    """The worker's ``unreal.pilot_others_margin_mb``: its default, asserted not overridden for the test worker."""
    from sethlans_worker_agent import config_unreal
    for env in (os.environ if environ is None else environ, worker_env or {}):
        assert not str(env.get(MARGIN_ENV, "")).strip(), f"{MARGIN_ENV} must not be set for this test"
    return next(default for key, default, _low, _high in config_unreal._INT_SETTINGS
                if key == "PILOT_OTHERS_MARGIN_MB")


def card_for_index(cards, index):
    """The ticked card whose DXGI index is ``index``; an AssertionError otherwise (never another card)."""
    found = [card for card in cards if index is not None and card.dxgi_index == index]
    assert len(found) == 1, f"The pilot's render launch uses adapter {index}, not one ticked card: {cards}"
    return found[0]


class OthersHolder:
    """Starts the holder once inside this machine's pilot render launch; ``stop`` ends it and returns its status."""

    def __init__(self, mb, cards, db_path, worker_id, out_dir, margin):
        self.mb, self.cards, self.db_path, self.worker_id = mb, tuple(cards), db_path, worker_id
        self.margin = margin  # the worker's pilot_others_margin_mb (pilot_margin_mb)
        self.status_path = Path(out_dir, "gpu_memory_holder.json")
        self.output_path = Path(out_dir, "gpu_memory_holder.txt")
        self.proc = self.trigger = None

    def poll(self):
        """One poll round: start the holder when this machine's pilot render launch is running."""
        if self.proc is not None:
            return
        for pid, job_id, attempt, index in render_launches():
            if rec.pilot_target(self.db_path, job_id) == self.worker_id:
                self._start(pid, job_id, attempt, card_for_index(self.cards, index))
                return

    def _start(self, ue_pid, job_id, attempt, card):
        self.trigger = {"ue_pid": ue_pid, "job": job_id, "attempt": attempt, "card": card.key,
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        command = [sys.executable, str(HOLDER), f"--card={card.key}", f"--mb={self.mb}",
                   f"--seconds={HOLD_SECONDS}", f"--status={self.status_path}", f"--watch-pid={ue_pid}",
                   f"--watch-pid={os.getpid()}"]
        with open(self.output_path, "w", encoding="utf-8") as out:  # a file, not a pipe (no pipe deadlock)
            self.proc = subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT, cwd=str(HOLDER.parents[2]))
        logger.info("Other program started inside pilot %s's render launch: %s", job_id, self.trigger)

    def status(self):
        """The holder's last status; a few short retries ride over a read during its atomic replace."""
        for attempt in range(STATUS_READ_TRIES):
            try:
                return json.loads(self.status_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {}
            except (OSError, ValueError):
                if attempt + 1 < STATUS_READ_TRIES:
                    time.sleep(0.1)
        return {}

    def stop(self):
        """Wait (bounded) for the holder to finish by itself, then kill whatever is left; its last status."""
        if self.proc is None:
            return {}
        deadline = time.monotonic() + STOP_WAIT_S
        while self.status().get("state") not in ("freed", "refused", "failed") and time.monotonic() < deadline:
            time.sleep(0.5)
        status = self.status()
        _kill_holder(status.get("pid"))
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # logged, never raised: stop() runs in the test's finally
            logger.warning("The holder launcher (pid %s) did not exit within 10 s after kill.", self.proc.pid)
        return status


def _kill_holder(pid):
    """Kill the holder's own process (the status pid; behind a venv launcher it is not ``Popen.pid``)."""
    import psutil
    try:
        proc = psutil.Process(pid) if pid else None
        if proc is not None and any(HOLDER.name in (part or "") for part in proc.cmdline()):
            proc.kill()
            proc.wait(timeout=10)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    except psutil.TimeoutExpired:  # logged, never raised: stop() runs in the test's finally
        logger.warning("The GPU memory holder (pid %s) did not exit within 10 s after kill.", pid)


def _int(value):
    return int(value) if str(value or "").strip().lstrip("-").isdigit() else None


def check_pilot(holder, status, pilot, worker_log, row):
    """Band 2 on this machine's AC-E7 pilot (see the module docstring); records the figures."""
    summary = parse_summary(pilot["last_output"])
    rec.record("AC-E7 other program", holder=status, trigger=holder.trigger, pilot=pilot["id"],
               summary=rec.summary_facts(summary), row_peak_mb=row.get("peak_mb"))
    trigger = holder.trigger or {}
    assert trigger.get("job") == pilot["id"], f"The holder never ran inside pilot {pilot['id']}'s render: {trigger}"
    assert status.get("state") == "freed" and status.get("allocated_mb") == holder.mb, f"Holder: {status}"
    assert summary.get("attempt") == trigger["attempt"], (
        f"Pilot {pilot['id']} ended on attempt {summary.get('attempt')}, not the holder's {trigger['attempt']}: "
        "it was released and ran again")
    released = f"Pilot job {pilot['id']} records no verdict"
    assert released not in worker_log, f"The worker released pilot {pilot['id']} (band 3)"
    rise, margin = _int(summary.get("pilot_others_rise_mb")), holder.margin
    assert rise is not None and rise > margin, f"pilot_others_rise_mb {rise} (margin {margin}): {summary}"
    band2 = BAND2_LINE.format(job=pilot["id"], rise=rise)
    assert any(band2 in line and BAND2_TAIL in line for line in worker_log.splitlines()), (
        f"No band 2 line for pilot {pilot['id']} in the worker log: {band2!r}")
    assert "pilot_others_added_mb" in summary, summary
    peak, card = int(summary["pilot_peak_mb"]), int(summary["pilot_card_delta_peak_mb"])
    csv = _int(summary.get("pilot_csv_peak_mb"))
    assert peak == max(card, csv or 0), f"peak {peak} is not max(card {card}, csv {csv})"
    assert summary.get("pilot_peak_source") == ("csv" if (csv or 0) > card else "card"), summary
    assert summary.get("pilot_verdict") == "fits" and summary.get("pilot_lower_bound_mb") == "", summary
    record = rec.pilot_record(holder.db_path, pilot["id"])
    assert (record["verdict"], record["peak_mb"], record["lower_bound_mb"]) == ("fits", peak, None), record
    assert (row.get("peak_mb") or 0) >= peak and row.get("lower_bound_mb") is None, f"Row {row}, pilot peak {peak}"
    return {"rise_mb": rise, "added_mb": _int(summary.get("pilot_others_added_mb"))}
