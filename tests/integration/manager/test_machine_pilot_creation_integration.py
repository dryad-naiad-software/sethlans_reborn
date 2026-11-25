# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: pilot creation per (worker, card kind) pair through
``services/machine_tests.ensure_machine_tests`` (Unreal per-machine memory
test D2, D5, D14, FR-MT-P-1).

Covers: every eligible online pair of a key gets exactly one targeted
pilot (two cards of one kind are one pair) and a second trigger adds
none; an offline worker and a worker with the VRAM override (D14) get
none; a pair that fits, does not fit, is testing or failed gets none (a
failed pair waits for the admin retry), while a ``retest_due`` pair gets a
new pilot and its old record stops being current; ``workers`` narrows the
pairs to one worker; the trigger asserts ``lock_project``.  The 20-pilot
cap is logged at WARNING at most once an hour per worker (the clock is the
module's own ``time.monotonic``), separately for each worker, and again
after ``reset_cap_warnings``.
"""

import types
from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from workers.constants import WORKER_STALENESS_SECONDS
from workers.models import Job, UnrealMeasurement, Worker
from workers.services import machine_tests
from workers.services.unreal_pilot import ProjectLockMissing, lock_project

from .machine_test_helpers import KIND_AMD, TARGET, current_of, kinds_worker, locked_ensure, pair_record
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import KIND, OTHER_SEQUENCE, ready_worker

OTHER_TARGET = TARGET.__class__(TARGET.engine_build, TARGET.map, OTHER_SEQUENCE, TARGET.preset)
STATES = {
    'fits': {'verdict': 'fits'},
    'does_not_fit': {'verdict': 'does_not_fit'},
    'testing': {'pilot': 'QUEUED'},
    'rendering': {'pilot': 'RENDERING'},
    'failed': {'pilot': 'ERROR', 'error': 'boom'},
}


@pytest.fixture
def uasset(db):
    return unreal_asset()


def _pairs(made):
    return sorted((p.target_worker_id, p.render_settings['unreal.card_kind']) for p in made)


@pytest.mark.django_db
class TestOnePilotPerPair:

    def test_every_online_pair_gets_one(self, uasset, worker_with_token, make_worker_client):
        worker_a = kinds_worker(worker_with_token[0], KIND, KIND_AMD, KIND)  # two cards of KIND: one pair
        worker_b = kinds_worker(make_worker_client('mt-pc-b')[0], KIND)
        made = locked_ensure(uasset)
        assert _pairs(made) == sorted([(worker_a.pk, KIND), (worker_a.pk, KIND_AMD), (worker_b.pk, KIND)])
        assert {p.status for p in made} == {'QUEUED'}
        assert locked_ensure(uasset) == []
        for worker, kind in ((worker_a, KIND), (worker_a, KIND_AMD), (worker_b, KIND)):
            [pk] = current_of(uasset, TARGET.sha256(), worker, kind)
            assert UnrealMeasurement.objects.get(pk=pk).pilot_job.target_worker_id == worker.pk

    def test_offline_and_override_workers_get_none(self, uasset, worker_with_token, make_worker_client):
        online = ready_worker(worker_with_token[0])
        offline = ready_worker(make_worker_client('mt-pc-off')[0])
        Worker.objects.filter(pk=offline.pk).update(
            last_seen=timezone.now() - timedelta(seconds=WORKER_STALENESS_SECONDS + 60))
        ready_worker(make_worker_client('mt-pc-ovr')[0], source='override')
        assert _pairs(locked_ensure(uasset)) == [(online.pk, KIND)]

    @pytest.mark.parametrize('state', sorted(STATES))
    def test_a_pair_with_a_current_record_gets_none(self, uasset, worker_with_token, state):
        worker = ready_worker(worker_with_token[0])
        record = pair_record(uasset, TARGET.sha256(), worker, **STATES[state])
        assert locked_ensure(uasset) == []
        assert current_of(uasset, TARGET.sha256(), worker) == [record.pk]

    def test_a_retest_due_pair_gets_a_new_pilot(self, uasset, worker_with_token):
        worker = ready_worker(worker_with_token[0])
        old = pair_record(uasset, TARGET.sha256(), worker, verdict='fits', stale='driver_changed')
        [pilot] = locked_ensure(uasset)
        assert current_of(uasset, TARGET.sha256(), worker) == [UnrealMeasurement.objects.get(pilot_job=pilot).pk]
        assert UnrealMeasurement.objects.get(pk=old.pk).current is False

    def test_workers_narrows_the_pairs(self, uasset, worker_with_token, make_worker_client):
        ready_worker(worker_with_token[0])
        worker_b = ready_worker(make_worker_client('mt-pc-nar')[0])
        with transaction.atomic():
            made = machine_tests.ensure_machine_tests(lock_project(uasset.project), uasset, TARGET,
                                                      reason='test', workers=[worker_b.pk])
        assert _pairs(made) == [(worker_b.pk, KIND)]

    def test_the_project_lock_is_required(self, uasset, worker_with_token):
        ready_worker(worker_with_token[0])
        with transaction.atomic(), pytest.raises(ProjectLockMissing):
            machine_tests.ensure_machine_tests(uasset.project, uasset, TARGET, reason='test')
        assert not Job.objects.filter(unreal_kind='pilot').exists()


@pytest.mark.django_db
class TestCapWarning:

    @pytest.fixture
    def capped(self, uasset, worker_with_token, make_worker_client, mocker):
        """Two eligible workers with 20 live QUEUED pilots each, a fake module clock and the logger mocked."""
        workers = [ready_worker(worker_with_token[0]), ready_worker(make_worker_client('mt-cap2-b')[0])]
        filler = unreal_asset(uasset.project, name='MtCapWarnFill')
        for worker in workers:
            for n in range(machine_tests.MAX_OPEN_PILOTS):
                pair_record(filler, f'{worker.pk:016x}{n:048x}', worker, pilot='QUEUED')
        clock = [1000.0]
        mocker.patch.object(machine_tests, 'time', types.SimpleNamespace(monotonic=lambda: clock[0]))
        warning = mocker.patch.object(machine_tests.logger, 'warning')
        return workers, clock, warning

    @staticmethod
    def _warned(warning):
        """The hostnames warned about since the last call (and forget them)."""
        names = sorted(call.args[1] for call in warning.call_args_list)
        for call in warning.call_args_list:
            assert 'already has %d queued memory tests' in call.args[0] and call.args[2] == 20
        warning.reset_mock()
        return names

    def test_logged_at_most_once_an_hour_per_worker(self, uasset, capped):
        (worker_a, worker_b), clock, warning = capped
        hosts = sorted([worker_a.hostname, worker_b.hostname])
        assert machine_tests.CAP_WARNING_SECONDS == 3600
        assert locked_ensure(uasset) == []
        assert self._warned(warning) == hosts
        assert locked_ensure(uasset, OTHER_TARGET) == []
        clock[0] += 3599
        assert locked_ensure(uasset) == []
        assert self._warned(warning) == []
        clock[0] += 2
        assert locked_ensure(uasset, OTHER_TARGET) == []
        assert self._warned(warning) == hosts

    def test_each_worker_has_its_own_hour(self, uasset, capped):
        (worker_a, worker_b), clock, warning = capped
        UnrealMeasurement.objects.filter(worker=worker_b, current=True).update(current=False)  # b below the cap
        assert [p.target_worker_id for p in locked_ensure(uasset)] == [worker_b.pk]
        assert self._warned(warning) == [worker_a.hostname]
        for n in range(machine_tests.MAX_OPEN_PILOTS):
            pair_record(uasset, f'{n:064x}', worker_b, pilot='QUEUED')
        clock[0] += 60
        assert locked_ensure(uasset, OTHER_TARGET) == []
        assert self._warned(warning) == [worker_b.hostname]

    def test_reset_forgets_the_last_warning(self, uasset, capped):
        _workers, _clock, warning = capped
        locked_ensure(uasset)
        assert len(self._warned(warning)) == 2
        machine_tests.reset_cap_warnings()
        locked_ensure(uasset, OTHER_TARGET)
        assert len(self._warned(warning)) == 2
