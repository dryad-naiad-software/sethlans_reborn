# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The fits-nowhere check of one measurement key (Unreal per-machine memory
test D12, D15, FR-MT-N-1).

:func:`check_on_commit` registers :func:`check` with
``transaction.on_commit``, so the check runs in its own transaction after
the caller's commit (D15: never inside the transaction that holds the Job,
Worker and record locks).  ``pilot-result/`` calls it after every stored
verdict.

**Placeholder:** :func:`check` is a no-op that stops nothing until the
fits-nowhere stop (FR-MT-N-1, D12) is built into it.
"""

from django.db import transaction


def check(project_id, asset_id, overrides, target_hash):
    """Stop the key's QUEUED jobs when no online machine fits it (D12); return the number stopped.

    Placeholder: stops nothing yet and returns 0.
    """
    return 0


def check_on_commit(project_id, asset_id, overrides, target_hash):
    """Run :func:`check` for the key after the current transaction commits."""
    transaction.on_commit(lambda: check(project_id, asset_id, overrides, target_hash), robust=True)
