# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Reset helper for worker job-claim state (Unreal foundation chunk 3).

Touches only modules that are already imported, so manager-only test
runs never import the worker package.
"""
import sys


def reset_worker_claim_state() -> None:
    job_processor = sys.modules.get('sethlans_worker_agent.job_processor')
    if job_processor is not None:
        with job_processor._active_jobs_lock:
            job_processor._unfinished_job_ids.clear()
    job_selection = sys.modules.get('sethlans_worker_agent.job_selection')
    if job_selection is not None:
        job_selection.reset_for_tests()
