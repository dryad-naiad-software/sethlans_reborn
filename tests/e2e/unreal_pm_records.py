# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
What the per-machine memory test E2E reads outside the API (chunk 9).

- **Records** (``key_records``, ``pilot_target``): the manager's own SQLite
  test database (the harness's ``test_e2e_db.sqlite3``), opened read-only.
  A record's ``verdict_source``, ``shared_peak_mb``, ``job_peak_max_mb``
  and a pilot's ``target_worker`` are not in any API response.
- **The one write** (``admit_record``): AC-E5 says the test sets the result
  peak of AC-E4's key so the gate admits a piece. It changes that one
  current ``does_not_fit`` record to ``fits`` with the given peak, clears
  its job peak and lower bound, and sets its ``shared_peak_mb`` (the
  piece's spill reference, D9) to the value the test chose; nothing else
  is written.
- **Driver lines** (``driver_lines``): an independent read of UE's
  ``Found D3D12 adapter`` / ``Driver Version`` / ``Chosen D3D12 Adapter Id``
  lines of a job's render log (not the worker's own parser), for AC-E1 and
  U-2.
- **Memory reports** (``memory_reports``): the worker's INFO line
  ``Unreal job <id> memory report sent (job peak from the <phase> launch):
  {...}`` (``unreal_executor.memory_report``), the only place the reported
  ``job_peak_mb`` is visible (AC-E6, U-4).
- **Results** (``record``): one JSON line per observation, appended to
  ``unreal_pm_config.results_path()`` and logged, for the run's report.
"""

import ast
import json
import logging
import re
import sqlite3
import time
from pathlib import Path

from tests.e2e import unreal_pm_config as pm

logger = logging.getLogger(__name__)

RECORD_COLUMNS = ("id", "pilot_job_id", "worker_id", "worker_hostname", "card_kind", "current", "verdict",
                  "verdict_source", "stale_reason", "peak_mb", "job_peak_max_mb", "lower_bound_mb",
                  "shared_peak_mb", "driver_version", "covered_by_id", "gpu_name", "gpu_total_mb",
                  "baseline_used_mb", "target_sha256")
# Summary keys copied into the result file for every job (absent keys are skipped).
SUMMARY_KEYS = ("kind", "card", "slot", "attempt", "warmup", "vram_gate", "vram_mb", "vram_used_mb",
                "vram_usable_mb", "vram_need_mb", "vram_measurement_id", "vram_card_kind", "vram_job_peak_mb",
                "vram_job_peak_phase", "vram_peak_mb", "spilled", "spill_cause", "spill_shared_peak_mb",
                "spill_reference_mb", "spill_others_peak_mb", "driver_version", "driver_source",
                "driver_changed", "memory_report", "pilot_verdict", "pilot_peak_mb", "pilot_baseline_mb",
                "pilot_card_delta_peak_mb", "pilot_csv_peak_mb", "pilot_peak_phase", "pilot_saturated",
                "pilot_lower_bound_mb", "pilot_others_rise_mb", "pilot_frames")
# Anchored at the line start (UE's optional "[time][frame]" prefix), so echoed text never matches.
_PREFIX = r"^(?:\[[^\]]*\]\[\s*\d+\])?"
_FOUND = re.compile(_PREFIX + r"LogD3D12RHI: Found D3D12 adapter (\d+): (.*?) \(VendorId")
_DRIVER = re.compile(_PREFIX + r"LogD3D12RHI:   Driver Version: .*\(internal:([^,)]+),")
_CHOSEN = re.compile(_PREFIX + r"LogD3D12RHI: Chosen D3D12 Adapter Id = (\d+)")
_REPORT = re.compile(r"Unreal job (\d+) memory report sent \(job peak from the (\w+) launch\): (\{.*\})")


def _connect(db_path, write=False):
    if write:
        return sqlite3.connect(str(db_path), timeout=30)
    return sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, timeout=30)


def _rows(db_path, sql, params):
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def key_records(db_path, job_id):
    """The current records of job ``job_id``'s key (a render job's own key; an audio job's submission piece key)."""
    sql = (f"SELECT {', '.join('m.' + c for c in RECORD_COLUMNS)} FROM workers_unrealmeasurement m "
           "WHERE m.asset_id = (SELECT asset_id FROM workers_job WHERE id = ?) AND m.target_sha256 = ("
           "SELECT COALESCE(NULLIF(s.piece_target_sha256, ''), j.unreal_target_sha256) FROM workers_job j "
           "LEFT JOIN workers_sequencesubmission s ON s.id = j.sequence_submission_id WHERE j.id = ?) "
           "AND m.current = 1 ORDER BY m.id")
    return _rows(db_path, sql, (job_id, job_id))


def record_by_id(db_path, record_id):
    rows = _rows(db_path, f"SELECT {', '.join(RECORD_COLUMNS)} FROM workers_unrealmeasurement WHERE id = ?",
                 (record_id,))
    assert rows, f"No record {record_id}"
    return rows[0]


def pilot_record(db_path, pilot_id):
    """The record pilot ``pilot_id`` measured for (``pilot_job``)."""
    rows = _rows(db_path, f"SELECT {', '.join(RECORD_COLUMNS)} FROM workers_unrealmeasurement WHERE pilot_job_id = ?",
                 (pilot_id,))
    assert len(rows) == 1, f"No single record for pilot {pilot_id}: {rows}"
    return rows[0]


def pilot_target(db_path, pilot_id):
    """The pilot job's ``target_worker_id`` (D4; not in the API)."""
    rows = _rows(db_path, "SELECT target_worker_id FROM workers_job WHERE id = ?", (pilot_id,))
    assert rows, f"No job {pilot_id}"
    return rows[0]["target_worker_id"]


def admit_record(db_path, record_id, peak_mb, shared_ref_mb):
    """AC-E5: the one current does_not_fit record becomes ``fits`` with result peak ``peak_mb``.

    ``shared_ref_mb`` becomes its ``shared_peak_mb``, the piece's spill reference (D9).
    """
    sql = ("UPDATE workers_unrealmeasurement SET verdict = 'fits', verdict_source = 'pilot', peak_mb = ?, "
           "job_peak_max_mb = NULL, lower_bound_mb = NULL, shared_peak_mb = ? WHERE id = ? AND current = 1 "
           "AND verdict = 'does_not_fit' AND stale_reason = ''")
    with _connect(db_path, write=True) as conn:
        changed = conn.execute(sql, (int(peak_mb), int(shared_ref_mb), int(record_id))).rowcount
    assert changed == 1, f"Record {record_id} was not a current does_not_fit record ({changed} rows changed)"


def driver_lines(log_path):
    """``(chosen adapter's internal: driver or None, every Driver Version line)`` of one UE log."""
    found, chosen, block, lines = {}, None, None, []
    with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = _FOUND.match(line)
            if match:
                block = int(match.group(1))
                found.setdefault(block, (match.group(2), None))
                continue
            match = _CHOSEN.match(line)
            if match:
                chosen = int(match.group(1)) if chosen is None else chosen
                block = None
                continue
            match = _DRIVER.match(line)
            if match:
                lines.append(line.strip()[:300])
                if block is not None and found[block][1] is None:
                    found[block] = (found[block][0], match.group(1))
    return (found.get(chosen, (None, None))[1] if chosen is not None else None), lines


def render_logs(cache_dir, job_id, attempt):
    """The job's render launch logs (``j\\<id>-<attempt>\\logs\\render.<n>.log``), oldest first."""
    return sorted(Path(cache_dir, "j", f"{int(job_id)}-{attempt}", "logs").glob("render.*.log"))


def memory_reports(worker_log_text):
    """``{job id: (phase, report body)}`` from the worker's ``memory report sent`` lines (the last per job)."""
    reports = {}
    for line in worker_log_text.splitlines():
        match = _REPORT.search(line)
        if match:
            reports[int(match.group(1))] = (match.group(2), ast.literal_eval(match.group(3)))
    return reports


def summary_facts(summary):
    """The ``SUMMARY_KEYS`` of a parsed job summary that it has."""
    return {key: summary[key] for key in SUMMARY_KEYS if key in summary}


def record(name, **data):
    """Append one observation to the result file and log it."""
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "name": name, **data}
    text = json.dumps(entry, default=str, sort_keys=True)
    logger.info("PM-E2E RESULT %s", text)
    try:
        with open(pm.results_path(), "a", encoding="utf-8") as out:
            out.write(text + "\n")
    except OSError as exc:  # the log line above still carries it
        logger.warning("Could not append to %s: %s", pm.results_path(), exc)
