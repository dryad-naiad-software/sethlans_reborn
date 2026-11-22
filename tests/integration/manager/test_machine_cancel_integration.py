# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: QUEUED targeted pilots that can no longer run are
canceled (``services/machine_test_cancel.cancel_ineligible_pilots``;
Unreal per-machine memory test D2, D5, D15, FR-MT-P-1).

Covers: after a card kind is un-ticked, the next pilot trigger (and the
next heartbeat sweep) cancels that kind's QUEUED pilot with "This machine
no longer uses this card kind.", its record stops being current so the
pair reads not tried (not failed), the other kind's pilot is kept, and
ticking the kind again gives a new pilot; the same message when the
worker is deactivated, stops reporting the build or the share, or has
only the VRAM override; an offline worker's pilot waits; a pilot whose
record was replaced is canceled as superseded even when its worker is
offline; a pilot made under other CVar overrides and a RENDERING pilot
are kept; the Job rows are locked ``SKIP LOCKED`` before the records (a
pilot row another transaction holds is skipped, not waited on);
canceling after the Worker rows are locked raises (D15).
"""

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.row_lock_sql import locked_tables
from workers.constants import WORKER_STALENESS_SECONDS
from workers.models import Job, UnrealMeasurement, Worker
from workers.services.machine_test_cancel import cancel_ineligible_pilots, release_records
from workers.services.machine_tests import pair_states
from workers.services.unreal_pilot import lock_project
from workers.services.worker_locks import lock_workers

from .machine_test_helpers import (
    KIND_AMD, NO_LONGER_USED, TARGET, emulated_row_locks, heartbeat, kind_card, kinds_worker, locked_ensure,
    pair_record, set_cards,
)
from .unreal_helpers import BUILD, caps, unreal_asset
from .unreal_pilot_helpers import KIND, live_card, record_of

SUPERSEDED = "Pilot is no longer needed: its target was measured by a later pilot."
OFFLINE = timezone.timedelta(seconds=WORKER_STALENESS_SECONDS + 60)


@pytest.fixture
def two_kinds(db, worker_with_token):
    """A worker with two ticked kinds and one QUEUED pilot per kind: ``(worker, client, {kind: pilot})``."""
    worker, client = worker_with_token
    asset = unreal_asset()
    kinds_worker(worker, KIND, KIND_AMD)
    made = {p.render_settings['unreal.card_kind']: p for p in locked_ensure(asset)}
    assert sorted(made) == sorted([KIND, KIND_AMD])
    return worker, client, made


def _untick_amd(worker):
    set_cards(worker, [kind_card(KIND, 0), kind_card(KIND_AMD, 1, ticked=False, state='unticked')])


def _cancel(asset):
    """Both steps under the D15 locks: cancel (Job rows), ``lock_workers()``, release the records; the pks."""
    with transaction.atomic():
        pks = cancel_ineligible_pilots(lock_project(asset.project))
        lock_workers()
        release_records(pks)
    return pks


def _state(pilot):
    pilot.refresh_from_db()
    return pilot.status, pilot.error_message


def _amd_state(worker, pilot):
    return pair_states(pilot.asset, pilot.asset.project.unreal_overrides_sha256, TARGET.sha256(),
                       [(worker, KIND_AMD)]).get((worker.pk, KIND_AMD), 'not_tried')


@pytest.mark.django_db
class TestUntickedKind:

    def test_the_next_trigger_cancels_it_and_a_retick_tests_again(self, two_kinds):
        worker, _client, made = two_kinds
        _untick_amd(worker)
        assert locked_ensure(made[KIND].asset) == []
        assert _state(made[KIND_AMD]) == ('CANCELED', NO_LONGER_USED)
        assert made[KIND_AMD].completed_at is not None
        assert record_of(made[KIND_AMD]).current is False
        assert _amd_state(worker, made[KIND_AMD]) == 'not_tried'
        assert _state(made[KIND]) == ('QUEUED', '')
        set_cards(worker, [kind_card(KIND, 0), kind_card(KIND_AMD, 1)])
        [again] = locked_ensure(made[KIND].asset)
        assert (again.target_worker_id, again.render_settings['unreal.card_kind']) == (worker.pk, KIND_AMD)
        assert _amd_state(worker, again) == 'testing'

    def test_the_heartbeat_sweep_cancels_it(self, two_kinds, django_capture_on_commit_callbacks):
        worker, client, made = two_kinds
        _untick_amd(worker)
        heartbeat(client, worker, django_capture_on_commit_callbacks)
        assert _state(made[KIND_AMD]) == ('CANCELED', NO_LONGER_USED)
        assert _state(made[KIND]) == ('QUEUED', '')
        assert _amd_state(worker, made[KIND_AMD]) == 'not_tried'

    def test_an_offline_worker_keeps_its_pilot(self, two_kinds):
        worker, _client, made = two_kinds
        _untick_amd(worker)
        Worker.objects.filter(pk=worker.pk).update(last_seen=timezone.now() - OFFLINE)
        assert locked_ensure(made[KIND].asset) == []
        assert _state(made[KIND_AMD]) == ('QUEUED', '')


@pytest.mark.django_db
class TestOtherReasons:

    @pytest.mark.parametrize('change', ['inactive', 'no_build', 'no_share', 'override'])
    def test_a_worker_that_no_longer_qualifies(self, two_kinds, change):
        worker, _client, made = two_kinds
        if change == 'inactive':
            Worker.objects.filter(pk=worker.pk).update(is_active=False)
        elif change == 'override':
            live_card(worker, source='override')
        else:
            changed = caps(builds=('5.7.4-46234231',)) if change == 'no_build' else caps(reachable=False)
            Worker.objects.filter(pk=worker.pk).update(unreal_capabilities=changed)
        assert locked_ensure(made[KIND].asset) == []
        assert {_state(p) for p in made.values()} == {('CANCELED', NO_LONGER_USED)}
        assert not UnrealMeasurement.objects.filter(pilot_job__in=made.values(), current=True).exists()

    def test_a_superseded_pilot_is_canceled_even_offline(self, two_kinds):
        worker, _client, made = two_kinds
        UnrealMeasurement.objects.filter(pilot_job=made[KIND_AMD]).update(current=False)
        Worker.objects.filter(pk=worker.pk).update(last_seen=timezone.now() - OFFLINE)
        assert _cancel(made[KIND].asset) == [made[KIND_AMD].pk]
        assert _state(made[KIND_AMD]) == ('CANCELED', SUPERSEDED)
        assert _state(made[KIND]) == ('QUEUED', '')

    def test_pilots_under_other_cvars_and_rendering_pilots_are_kept(self, db, worker_with_token):
        worker = kinds_worker(worker_with_token[0], KIND)
        asset = unreal_asset()
        other_cvars = pair_record(asset, 'a' * 64, worker, pilot='QUEUED', overrides='f' * 64)
        rendering = pair_record(asset, 'b' * 64, worker, pilot='RENDERING')
        assert _cancel(asset) == []
        assert Job.objects.get(pk=other_cvars.pilot_job_id).status == 'QUEUED'
        # Un-ticked: the QUEUED pilot goes, the RENDERING one is never touched.
        set_cards(worker, [kind_card(KIND, 0, ticked=False, state='unticked')])
        assert _cancel(asset) == [other_cvars.pilot_job_id]
        assert Job.objects.get(pk=other_cvars.pilot_job_id).status == 'CANCELED'
        assert Job.objects.get(pk=rendering.pilot_job_id).status == 'RENDERING'
        assert UnrealMeasurement.objects.get(pk=rendering.pk).current is True
        assert other_cvars.engine_build == BUILD

    def test_a_locked_pilot_row_is_skipped_not_waited_on(self, two_kinds):
        """Job rows ``SKIP LOCKED``, then the Worker rows, then the records (SQL shape: SQLite has no row locks)."""
        worker, _client, made = two_kinds
        _untick_amd(worker)
        with emulated_row_locks(), CaptureQueriesContext(connection) as ctx:
            assert _cancel(made[KIND].asset) == [made[KIND_AMD].pk]
        tables = [(locked_tables(q['sql']), 'SKIP LOCKED' in q['sql'])
                  for q in ctx.captured_queries if 'FOR UPDATE' in q['sql']]
        assert tables == [({'workers_project'}, False), ({'workers_job'}, True), ({'workers_worker'}, False),
                          ({'workers_unrealmeasurement'}, False)], tables
        assert record_of(made[KIND_AMD]).current is False

    def test_releasing_before_the_worker_lock_refuses(self, two_kinds):
        _worker, _client, made = two_kinds
        with transaction.atomic(), pytest.raises(RuntimeError, match='after lock_workers'):
            release_records([made[KIND].pk])

    def test_after_the_worker_lock_it_refuses(self, two_kinds):
        _worker, _client, made = two_kinds
        with transaction.atomic():
            project = lock_project(made[KIND].asset.project)
            lock_workers()
            with pytest.raises(RuntimeError, match='before lock_workers'):
                cancel_ineligible_pilots(project)
