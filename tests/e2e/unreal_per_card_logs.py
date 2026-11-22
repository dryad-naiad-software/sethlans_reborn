# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Checks on the Unreal logs a job leaves in the worker's cache (Unreal render
per card AC-E1, AC-E4, AC-E9).

The worker keeps the newest ``unreal.keep_job_logs`` job folders (10 by
default) as ``CACHE_DIR\\j\\<job id>-<attempt>\\logs\\<phase>.<n>.log``
(UE's ``-abslog``); the job summary names the attempt (``attempt=``). This
run has five Unreal jobs, so every folder is still there.

- **Adapter (AC-E1):** every warm-up and render log has UE's
  ``Chosen D3D12 Adapter Id = N`` line (the worker's own anchored pattern,
  ``pilot.CHOSEN_ADAPTER``), and ``N`` is the DXGI index of the job's card.
- **Zen (AC-E4):** each log passes the worker's own Zen log rules for the
  job's slot (``zen_log.verify_log``: the slot's data folder, a zenserver
  launch or attach on the slot's port only), and has no
  ``Found locked but invalid lock file`` line and no
  ``LogZenServiceInstance`` line about shutting a zenserver down
  (``ZenServerInterface.cpp`` lines listed in the spec).
- **One claim per job (AC-E9):** each job has exactly one job folder
  created during this run, so no job was claimed twice (a local conflict
  sends a job back to the queue). Every run starts a fresh manager database
  (job ids from 1) while the cache keeps older runs' folders, so only
  folders created since the test started count.
"""

import os
from pathlib import Path

INVALID_LOCK = "Found locked but invalid lock file"
ZEN_CATEGORY = "LogZenServiceInstance"
# ZenServerInterface.cpp (UE 5.8.4) :1821 "Requesting shut down of zenserver process", :1840/:1891/:1899
# "... shut down zenserver ...", :1868 "After failed shut down request, attempting forced termination",
# :4187 "Found existing instance ... with different run context, will attempt shut down".
SHUT_DOWN_MARKS = ("shut down zenserver", "shut down of zenserver", "attempting forced termination",
                   "will attempt shut down")
ADAPTER_PHASES = ("warmup", "render")


def job_dirs(cache_dir, job_id, since):
    """This run's folders of ``job_id``: created at or after ``since`` (``time.time()``, 2 s slack)."""
    return sorted(path for path in Path(cache_dir, "j").glob(f"{int(job_id)}-*")
                  if os.stat(path).st_ctime >= since - 2)


def _lines(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            yield line.rstrip("\r\n")


def chosen_adapters(path):
    """Every ``Chosen D3D12 Adapter Id`` of one log, in order."""
    from sethlans_worker_agent.unreal_executor.pilot import CHOSEN_ADAPTER
    found = []
    for line in _lines(path):
        match = CHOSEN_ADAPTER.search(line)
        if match:
            found.append(int(match.group(1)))
    return found


def zen_problems(path):
    """The lines of one log that show shared Zen state touched."""
    return [line[:300] for line in _lines(path)
            if INVALID_LOCK in line or (ZEN_CATEGORY in line and any(m in line.lower() for m in SHUT_DOWN_MARKS))]


def check_job(cache_dir, job, summary, dxgi_index, zen_port, since):
    """Problems found in the logs of one job; ``[]`` when it passes. ``since``: the test's start."""
    from sethlans_worker_agent.unreal_executor import zen_log
    name, slot = job["name"], int(summary["slot"])
    folders = job_dirs(cache_dir, job["id"], since)
    problems = [] if len(folders) == 1 else [f"{name}: {len(folders)} job folders (claimed more than once?)"]
    logs = Path(cache_dir, "j", f"{job['id']}-{summary.get('attempt', '')}", "logs")
    paths = sorted(logs.glob("*.log"))
    if not paths:
        return problems + [f"{name}: no Unreal logs in {logs}"]
    for path in paths:
        phase = path.name.split(".", 1)[0]
        if phase in ADAPTER_PHASES:
            chosen = chosen_adapters(path)
            if not chosen or set(chosen) != {dxgi_index}:
                problems.append(f"{name} {path.name}: chosen adapters {chosen}, card's DXGI index {dxgi_index}")
        result = zen_log.verify_log(path, cache_dir, zen_port, phase, slot=slot)
        if not result.ok:
            problems.append(f"{name} {path.name}: Zen {result.detail}")
        problems.extend(f"{name} {path.name}: {line}" for line in zen_problems(path))
    return problems
