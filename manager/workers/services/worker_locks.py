# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Worker step of the D15 lock order for per-machine memory tests
(Unreal per-machine memory test D15, NF-1).

D15 orders every path Project -> Job rows -> Worker -> UnrealMeasurement
records -> SequenceDiscovery -> SequenceSubmission.  ``lock_project``
serializes writes within one project only, so two transactions on
different projects could lock two workers in opposite orders when each
creates pilots for several keys.  :func:`lock_workers` therefore locks
**every** Worker row once per transaction, in ascending pk order, before
the transaction's first record lock; later calls in the same transaction
reuse that set.  Pilot creation only uses workers in the set (a worker
added after the lock waits for a later trigger).

The marker follows ``unreal_pilot.lock_project``: it counts only while its
``on_commit`` token is pending in the current transaction (a rollback of
the transaction or of the savepoint the lock was taken in discards it).
"""

from django.db import transaction

from ..models import Worker
from .row_locks import lock_own_rows
from .unreal_pilot import LockToken

_MARKER = '_sethlans_unreal_worker_locks'


class _WorkerLockToken(LockToken):
    """``on_commit`` callback marking the Worker locks of one transaction."""

    def __call__(self):
        entry = getattr(self.connection, _MARKER, None)
        if entry is not None and entry[0] is self:
            setattr(self.connection, _MARKER, None)


def lock_workers():
    """Lock every Worker row ascending by pk, once per transaction; return the locked pks.

    Raises:
        RuntimeError: called outside ``transaction.atomic()``.
    """
    connection = transaction.get_connection()
    if not connection.in_atomic_block:
        raise RuntimeError("lock_workers must run inside transaction.atomic()")
    entry = getattr(connection, _MARKER, None)
    if entry is not None and entry[0].is_live():
        return entry[1]
    pks = frozenset(lock_own_rows(Worker.objects).order_by('pk').values_list('pk', flat=True))
    token = _WorkerLockToken(connection, None)
    transaction.on_commit(token)
    setattr(connection, _MARKER, (token, pks))
    return pks
