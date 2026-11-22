# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the routing note's memory part (Unreal per-machine
memory test FR-MT-U-2, chunk 8), over the online eligible pairs.

Covers: null as soon as one online pair fits, whatever the others; the
waiting note while any online pair is testing, not tried or re-test due,
counting distinct machines (two card kinds of one machine count once);
the failed note (with its reason and the retry route, and the
infrastructure sentence after the requeue limit) when no pair waits and
one failed; the too-big note when every online pair does not fit; the
no-machine note when no online worker is eligible.  An offline machine's
or a disabled machine's fits result, and a result of a kind the machine
no longer ticks, do not end the wait.  The job list gives each job its
own key's note.
"""

import pytest

from workers.models import Worker

from .fits_nowhere_helpers import dnf_record
from .machine_rows_helpers import (
    NO_MACHINE, TOO_BIG, WAITING, failed_text, key_args, keyed_job, machine, note,
)
from .machine_test_helpers import KIND_AMD, pair_record
from .unreal_helpers import SHARE, set_share
from .unreal_pilot_helpers import JOBS, KIND, settings

pytestmark = pytest.mark.django_db
REQUEUE = "Max auto-requeue attempts exceeded (3)"
INFRA = (" The pilot was released or requeued too often; this points to a worker "
         "or network problem, not to the project.")


@pytest.fixture
def job(db):
    return keyed_job()


class TestNoNote:

    def test_one_online_fits_ends_the_note(self, admin_client, job):
        fits, waiting, failed = machine('host-a'), machine('host-b'), machine('host-c')
        pair_record(*key_args(job), fits, verdict='fits')
        pair_record(*key_args(job), waiting, pilot='QUEUED')
        pair_record(*key_args(job), failed, pilot='ERROR', error='boom')
        assert note(admin_client, job) is None

    def test_a_fitting_second_kind_is_enough(self, admin_client, job):
        worker = machine('host-a', KIND, KIND_AMD)
        dnf_record(*key_args(job), worker)
        pair_record(*key_args(job), worker, kind=KIND_AMD, verdict='fits')
        assert note(admin_client, job) is None


class TestWaiting:

    def test_untried_machines_are_counted(self, admin_client, job):
        machine('host-a')
        machine('host-b')
        assert note(admin_client, job) == WAITING.format(n=2)

    def test_two_kinds_of_one_machine_count_once(self, admin_client, job):
        worker = machine('host-a', KIND, KIND_AMD)
        pair_record(*key_args(job), worker, pilot='RENDERING')
        assert note(admin_client, job) == WAITING.format(n=1)

    @pytest.mark.parametrize('state', [{'pilot': 'QUEUED'}, {'verdict': 'fits', 'stale': 'driver_changed'}],
                             ids=['testing', 'retest-due'])
    def test_waiting_wins_over_failed_and_does_not_fit(self, admin_client, job, state):
        waiting, failed, too_big = machine('host-a'), machine('host-b'), machine('host-c')
        pair_record(*key_args(job), waiting, **state)
        pair_record(*key_args(job), failed, pilot='CANCELED')
        dnf_record(*key_args(job), too_big)
        assert note(admin_client, job) == WAITING.format(n=1)

    def test_offline_and_disabled_fits_do_not_end_the_wait(self, admin_client, job):
        off = machine('host-off', online=False)
        disabled = machine('host-disabled', active=False)
        pair_record(*key_args(job), off, verdict='fits')
        pair_record(*key_args(job), disabled, verdict='fits')
        machine('host-on')
        assert note(admin_client, job) == WAITING.format(n=1)

    def test_a_result_of_a_kind_no_longer_ticked_does_not_end_the_wait(self, admin_client, job):
        worker = machine('host-a', KIND_AMD)
        pair_record(*key_args(job), worker, kind=KIND, verdict='fits')
        assert note(admin_client, job) == WAITING.format(n=1)


class TestEndStates:

    def test_failed_note_when_nothing_waits(self, admin_client, job):
        failed, too_big = machine('host-a'), machine('host-b')
        pair_record(*key_args(job), failed, pilot='ERROR', error='y' * 250)
        dnf_record(*key_args(job), too_big)
        assert note(admin_client, job) == failed_text('y' * 200, job.asset.project_id)

    def test_failed_note_after_the_requeue_limit_names_the_infrastructure(self, admin_client, job):
        pair_record(*key_args(job), machine('host-a'), pilot='ERROR', error=REQUEUE)
        assert note(admin_client, job) == failed_text(REQUEUE, job.asset.project_id) + INFRA

    def test_too_big_when_every_online_pair_does_not_fit(self, admin_client, job):
        worker = machine('host-a', KIND, KIND_AMD)
        dnf_record(*key_args(job), worker)
        dnf_record(*key_args(job), worker, kind=KIND_AMD)
        dnf_record(*key_args(job), machine('host-b'))
        assert note(admin_client, job) == TOO_BIG

    def test_no_machine_when_no_online_worker_is_eligible(self, admin_client, job):
        set_share(SHARE)
        off = machine('host-off', online=False)
        pair_record(*key_args(job), off, verdict='fits')
        on = machine('host-on')
        Worker.objects.filter(pk=on.pk).update(card_status={})
        assert note(admin_client, job) == NO_MACHINE


def test_job_list_gives_each_job_its_own_keys_note(admin_client, job):
    worker = machine('host-a')
    pair_record(*key_args(job), worker, verdict='fits')
    too_big = keyed_job(job.asset, name='RowsTooBig', render_settings=settings(sequence='/Game/Big.Big'))
    dnf_record(*key_args(too_big), worker)
    waiting = keyed_job(job.asset, name='RowsWait', render_settings=settings(sequence='/Game/Wait.Wait'))
    notes = {row['id']: row['routing_note'] for row in admin_client.get(JOBS, {'renderer': 'UNREAL'}).data}
    assert notes == {job.pk: None, too_big.pk: TOO_BIG, waiting.pk: WAITING.format(n=1)}
