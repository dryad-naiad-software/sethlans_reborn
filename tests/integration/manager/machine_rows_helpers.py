# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the per-machine memory test chunk 8 integration tests
(Unreal per-machine memory test FR-MT-U-1..3, NF-3: the machine list and
the routing note's memory part, ``services/machine_rows.py``).  Not a
test module.

:func:`machine` makes an eligible worker (D5) with one ticked ``ok`` card
per kind, online, offline or disabled; :func:`keyed_job` a QUEUED Unreal
render job made through the ORM (no pilot), whose key the tests fill with
:func:`machine_test_helpers.pair_record` rows.  The note texts are written
out from the spec and the task, not imported from the product.
"""

from types import SimpleNamespace

from rest_framework.authtoken.models import Token

from workers.models import Worker

from .fits_nowhere_helpers import go_offline
from .machine_test_helpers import kinds_worker, render_job
from .unreal_helpers import BUILD, gpu_memory, unreal_asset
from .unreal_pilot_helpers import JOBS, KIND

ROW_KEYS = {
    'worker', 'hostname', 'online', 'card_kind', 'card_name', 'card_total_mb', 'state', 'peak_mb',
    'lower_bound_mb', 'driver_version', 'reason', 'covered', 'ram',
}
# The name ``card_report_helpers.card()`` gives every stored card; ``ready_worker``'s reading total.
STORED_NAME = 'NVIDIA GeForce RTX 5090'
STORED_TOTAL = 32607
PROJECTS = '/api/projects/'
WAITING = "Waiting for memory tests on {n} machine(s)."
NO_MACHINE = (f"No online worker with Unreal Engine build {BUILD} has a ticked graphics card "
              "with a live memory reading.")
TOO_BIG = "This render does not fit the graphics cards of any machine that tested it."


def failed_text(reason, project_id):
    """FR-RT-9 note 3a (the failed note) for ``reason``."""
    return (f"This project's memory measurement failed: {reason}. "
            f"An admin can retry it (POST /api/projects/{project_id}/unreal-measurement/retry/).")


def machine(hostname, *kinds, totals=None, online=True, active=True):
    """An eligible worker with one ticked ``ok`` card per kind (``KIND`` when none given)."""
    worker = Worker.objects.create(hostname=hostname, is_active=True)
    kinds_worker(worker, *(kinds or (KIND,)))
    if totals:
        worker.gpu_memory = gpu_memory(*totals)
        worker.save(update_fields=['gpu_memory', 'last_seen'])
    if not online:
        go_offline(worker)
    if not active:
        Worker.objects.filter(pk=worker.pk).update(is_active=False)
    worker.refresh_from_db()
    return worker


def keyed_job(asset=None, name='RowsJob', **extra):
    """A QUEUED Unreal render job (ORM, no pilot), refreshed so its key is set."""
    job = render_job(asset or unreal_asset(), name=name, **extra)
    job.refresh_from_db()
    return job


def key_args(job):
    """``(asset, target hash)`` of a render job, as ``pair_record`` takes them."""
    return job.asset, job.unreal_target_sha256


def detail(client, job):
    resp = client.get(f'{JOBS}{job.pk}/')
    assert resp.status_code == 200, resp.data
    return resp.data


def machines(client, job):
    """``unreal_machines`` of the job detail."""
    return detail(client, job)['unreal_machines']


def note(client, job):
    return detail(client, job)['routing_note']


def row_of(rows, worker, kind=KIND):
    """The one row of ``(worker, kind)``."""
    [row] = [row for row in rows if row['worker'] == getattr(worker, 'pk', worker) and row['card_kind'] == kind]
    return row


def pairs(rows):
    """``[(hostname, card kind, state, online)]`` in response order."""
    return [(row['hostname'], row['card_kind'], row['state'], row['online']) for row in rows]


def worker_context(worker_user):
    """A serializer context whose request is a worker-token request (``_is_worker_request``)."""
    token, _ = Token.objects.get_or_create(user=worker_user)
    request = SimpleNamespace(auth=token, user=worker_user)
    return {'request': request, 'view': SimpleNamespace(action='retrieve')}
