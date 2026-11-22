# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: D3 on ``pilot-result/`` (Unreal per-machine memory test
D3, FR-MT-V-3, AC-6 for the pilot path).

**Trigger:** some current record of the same worker and card kind has a
non-null driver that differs from the reported non-null driver.
**Effect:** every current record of that pair whose driver is not the
reported one is marked ``stale_reason='driver_changed'`` (its pair then
reads ``retest_due``), whatever its verdict, key or project, records
stored with an unknown driver ('') included.  Nothing else is touched:
another worker's or another kind's records, records that are no longer
current, records with the reported driver, the pilot's own record.  No
trigger (only '' or equal-driver records), a null driver (UE's
``Unknown`` included) and a refused result stale nothing; when the
pilot's record is no longer current its figures are still written but D3
is skipped.  A staled record is no longer sent in the job payload and the
claim names the re-test.
"""

import pytest

from workers.models import UnrealMeasurement

from .machine_test_helpers import KIND_AMD, RETEST, kind_card, pair_record, render_job, set_cards
from .machine_verdict_helpers import NEW_DRIVER, OLD_DRIVER, dnf_body, fresh, state_of
from .unreal_helpers import unreal_asset, unreal_project
from .unreal_pilot_helpers import (
    JOBS, KIND, OTHER_SEQUENCE, claim, claimed_pilot, pilot_body, post_result, ready_worker, record_of, settings,
)

OTHER_KEY = 'e1' * 32
THIRD_KEY = 'e2' * 32


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def pilot(admin_client, asset, worker_with_token):
    worker, client = worker_with_token
    return claimed_pilot(admin_client, asset, worker, client), worker, client


@pytest.fixture
def other_project_asset(db):
    return unreal_asset(unreal_project(name='MtDriverOther'), name='MtDriverOtherAsset')


def _pair_records(asset, other_asset, worker, driver=OLD_DRIVER):
    """Current records of the pilot's pair (worker, KIND) in other keys.

    fits, does_not_fit and another project's fits with ``driver``, then a fits record with an unknown driver.
    """
    return [
        pair_record(asset, OTHER_KEY, worker, verdict='fits', driver_version=driver),
        pair_record(asset, THIRD_KEY, worker, verdict='does_not_fit', driver_version=driver),
        pair_record(other_asset, OTHER_KEY, worker, verdict='fits', driver_version=driver),
        pair_record(other_asset, THIRD_KEY, worker, verdict='fits', driver_version=''),
    ]


def _stale_reasons(records):
    return [fresh(record).stale_reason for record in records]


@pytest.mark.django_db
class TestDriverChange:

    @pytest.mark.parametrize('body', [pilot_body(driver_version=NEW_DRIVER), dnf_body(driver_version=NEW_DRIVER)],
                             ids=['pilot-fits', 'pilot-does-not-fit'])
    def test_stales_every_current_record_of_the_pair(self, pilot, asset, other_project_asset, body):
        job, worker, client = pilot
        records = _pair_records(asset, other_project_asset, worker)
        assert post_result(client, job.pk, body).status_code == 200
        assert _stale_reasons(records) == ['driver_changed'] * 4
        assert [state_of(record) for record in records] == ['retest_due'] * 4
        # The staled records keep their verdicts and drivers; only the stale reason changes.
        assert [(fresh(r).verdict, fresh(r).driver_version, fresh(r).current) for r in records] == [
            ('fits', OLD_DRIVER, True), ('does_not_fit', OLD_DRIVER, True), ('fits', OLD_DRIVER, True),
            ('fits', '', True)]
        own = record_of(job)
        assert (own.stale_reason, own.driver_version, own.current) == ('', NEW_DRIVER, True)
        assert state_of(own) == body['verdict']

    def test_touches_only_the_pairs_current_records_of_another_driver(self, pilot, asset, make_worker_client):
        job, worker, client = pilot
        other, _other_client, _token = make_worker_client('mt-driver-other')
        ready_worker(other)
        set_cards(worker, [kind_card(KIND, 0), kind_card(KIND_AMD, 1)])
        untouched = [
            pair_record(asset, OTHER_KEY, other, verdict='fits', driver_version=OLD_DRIVER),
            pair_record(asset, OTHER_KEY, worker, KIND_AMD, verdict='fits', driver_version=OLD_DRIVER),
            pair_record(asset, THIRD_KEY, worker, verdict='fits', driver_version=OLD_DRIVER, current=False),
            pair_record(asset, OTHER_KEY, worker, verdict='fits', driver_version=NEW_DRIVER),
        ]
        trigger = pair_record(asset, 'e4' * 32, worker, verdict='fits', driver_version=OLD_DRIVER)
        assert post_result(client, job.pk, pilot_body(driver_version=NEW_DRIVER)).status_code == 200
        assert fresh(trigger).stale_reason == 'driver_changed'
        assert _stale_reasons(untouched) == [''] * 4
        assert [state_of(record) for record in (untouched[0], untouched[1], untouched[3])] == ['fits'] * 3

    def test_a_staled_record_leaves_the_payload_and_the_claim_names_the_retest(self, pilot, asset):
        job, worker, client = pilot
        # Another key of the same pair (its own sequence), with a fits result under the old driver.
        normal = render_job(asset, name='MtDriverNormal', render_settings=settings(sequence=OTHER_SEQUENCE))
        staled = pair_record(asset, normal.unreal_target_sha256, worker, verdict='fits', driver_version=OLD_DRIVER)
        assert [r['measurement_id'] for r in client.get(f'{JOBS}{normal.pk}/').data['unreal_machine_results']] == [
            staled.pk]
        assert post_result(client, job.pk, pilot_body(driver_version=NEW_DRIVER)).status_code == 200
        assert client.get(f'{JOBS}{normal.pk}/').data['unreal_machine_results'] == []
        assert claim(client, normal, worker).data == {'error': RETEST}


@pytest.mark.django_db
class TestNoDriverChange:

    @pytest.mark.parametrize('driver', [None, 'Unknown', OLD_DRIVER], ids=['null', 'unknown', 'same'])
    def test_stales_nothing(self, pilot, asset, other_project_asset, driver):
        job, worker, client = pilot
        records = _pair_records(asset, other_project_asset, worker)
        assert post_result(client, job.pk, pilot_body(driver_version=driver)).status_code == 200
        assert _stale_reasons(records) == [''] * 4
        assert record_of(job).driver_version == (driver if driver == OLD_DRIVER else '')

    @pytest.mark.parametrize('drivers', [('',), ('', ''), (NEW_DRIVER, ''), (NEW_DRIVER,)],
                             ids=['unknown', 'two-unknown', 'equal-and-unknown', 'equal'])
    def test_no_trigger_without_another_known_driver(self, pilot, asset, drivers):
        job, worker, client = pilot
        records = [pair_record(asset, f'f{i}' * 32, worker, verdict='fits', driver_version=driver)
                   for i, driver in enumerate(drivers)]
        assert post_result(client, job.pk, pilot_body(driver_version=NEW_DRIVER)).status_code == 200
        assert _stale_reasons(records) == [''] * len(records)
        assert record_of(job).driver_version == NEW_DRIVER

    def test_a_refused_result_stales_nothing(self, pilot, asset, other_project_asset):
        job, worker, client = pilot
        records = _pair_records(asset, other_project_asset, worker)
        resp = post_result(client, job.pk, pilot_body(driver_version=NEW_DRIVER, card_kind=KIND_AMD))
        assert resp.status_code == 400
        assert _stale_reasons(records) == [''] * 4

    def test_a_superseded_pilot_record_writes_only_itself(self, pilot, asset, other_project_asset):
        job, worker, client = pilot
        records = _pair_records(asset, other_project_asset, worker)
        UnrealMeasurement.objects.filter(pilot_job=job).update(current=False)
        resp = post_result(client, job.pk, dnf_body(driver_version=NEW_DRIVER, shared_peak_mb=300))
        assert (resp.status_code, resp.data) == (200, {'status': 'DONE', 'peak_mb': 8102})
        own = record_of(job)
        assert (own.peak_mb, own.verdict, own.verdict_source, own.lower_bound_mb) == (8102, 'does_not_fit', 'pilot', 8102)
        assert (own.driver_version, own.shared_peak_mb, own.current) == (NEW_DRIVER, 300, False)
        assert _stale_reasons(records) == [''] * 4
