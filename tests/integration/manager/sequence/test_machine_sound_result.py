# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the sound job and its machine's result of the piece key
(Unreal per-machine memory test D10, D11, FR-MT-V-3, AC-9).

Covers: a sound (audio) job is offered only to a machine with a current,
not stale, ``fits`` result of its submission's piece key, and its job
payload and claim carry exactly that result (id, peak, RAM), never a
result of another key; the piece key's own pilot finishing ``fits`` on
``pilot-result/`` opens the sound jobs with that pilot's record, finishing
``does_not_fit`` keeps them closed; a driver change reported by another
pilot of the same machine and kind closes them again (``retest_due``).
"""

import pytest

from shared.unreal.validation import target_sha256

from workers.models import UnrealMeasurement

from ..machine_test_helpers import DOES_NOT_FIT, RETEST, pair_record
from ..unreal_helpers import BUILD, unreal_asset
from ..unreal_pilot_helpers import (
    JOBS, KIND, PILOT_RAM, claim, fits_record, pilot_body, post_result, ready_worker, record_of,
)
from .sequence_helpers import MAP, PRESET, SEQUENCE, poll_ids
from .sound_helpers import audio_jobs_of
from .submission_helpers import done_discovery, submit

RAM = {'prep': {'ws_mb': 4000, 'commit_mb': 8000}, 'warmup': None, 'render': {'ws_mb': 6000, 'commit_mb': 19000}}


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.fixture
def sound(admin_client, asset, worker_with_token):
    """A submission with sound whose piece key has a testing pilot for the one machine."""
    worker, client = worker_with_token
    done_discovery(admin_client, asset, worker, client)
    submission, _ = submit(admin_client, asset, sound=True)
    submission.refresh_from_db()
    ready_worker(worker)
    return submission, audio_jobs_of(submission), worker, client


def _piece_pilot(submission, worker):
    """The machine's own pilot of the piece key (its record is the pair's current one)."""
    record = UnrealMeasurement.objects.get(target_sha256=submission.piece_target_sha256, worker=worker,
                                           card_kind=KIND, current=True)
    return record.pilot_job


def _claimed_piece_pilot(submission, worker, client):
    pilot = _piece_pilot(submission, worker)
    resp = claim(client, pilot, worker)
    assert resp.status_code == 200, resp.data
    return pilot


def _results(client, job):
    resp = client.get(f'{JOBS}{job.pk}/')
    assert resp.status_code == 200, resp.data
    return resp.data['unreal_machine_results']


@pytest.mark.django_db
class TestSoundJobCarriesThePieceResult:

    def test_offered_and_carried_only_with_the_piece_keys_result(self, asset, sound):
        submission, jobs, worker, client = sound
        standalone = target_sha256(BUILD, MAP, SEQUENCE, PRESET)
        fits_record(asset, standalone, worker, peak_mb=12000, ram=None)
        assert not {job.pk for job in jobs} & set(poll_ids(client))
        piece = fits_record(asset, submission.piece_target_sha256, worker, peak_mb=9100, ram=RAM,
                            shared_peak_mb=128, driver_version='32.0.16.1692')
        expected = [{'measurement_id': piece.pk, 'card_kind': KIND, 'peak_mb': 9100, 'ram': RAM,
                     'shared_peak_mb': 128, 'driver_version': '32.0.16.1692'}]
        assert {job.pk for job in jobs} <= set(poll_ids(client))
        assert _results(client, jobs[0]) == expected
        resp = claim(client, jobs[0], worker)
        assert resp.status_code == 200, resp.data
        assert resp.data['unreal_machine_results'] == expected

    def test_a_staled_piece_result_closes_the_sound_jobs(self, asset, sound):
        submission, jobs, worker, client = sound
        fits_record(asset, submission.piece_target_sha256, worker, stale_reason='driver_changed')
        assert not {job.pk for job in jobs} & set(poll_ids(client))
        assert _results(client, jobs[0]) == []
        assert claim(client, jobs[0], worker).data == {'error': RETEST}


@pytest.mark.django_db
class TestPieceKeyPilotResult:

    def test_a_fits_pilot_opens_the_sound_jobs_with_its_record(self, sound):
        submission, jobs, worker, client = sound
        assert claim(client, jobs[0], worker).status_code == 409
        pilot = _claimed_piece_pilot(submission, worker, client)
        assert post_result(client, pilot.pk, pilot_body()).status_code == 200
        [entry] = _results(client, jobs[0])
        assert (entry['measurement_id'], entry['peak_mb'], entry['ram']) == (record_of(pilot).pk, 8102, PILOT_RAM)
        assert {job.pk for job in jobs} <= set(poll_ids(client))

    def test_a_does_not_fit_pilot_keeps_them_closed(self, sound):
        submission, jobs, worker, client = sound
        pilot = _claimed_piece_pilot(submission, worker, client)
        body = pilot_body(verdict='does_not_fit', lower_bound_mb=8102)
        assert post_result(client, pilot.pk, body).status_code == 200
        assert not {job.pk for job in jobs} & set(poll_ids(client))
        assert claim(client, jobs[0], worker).data == {'error': DOES_NOT_FIT}

    def test_another_pilots_new_driver_closes_them_again(self, asset, sound):
        submission, jobs, worker, client = sound
        piece = fits_record(asset, submission.piece_target_sha256, worker, driver_version='31.0.15.5222')
        # Another key's pilot of the same machine and kind, still running.
        other = pair_record(asset, 'e3' * 32, worker, pilot='RENDERING')
        other.pilot_job.assigned_worker = worker
        other.pilot_job.render_settings = {**other.pilot_job.render_settings, 'unreal.card_kind': KIND}
        other.pilot_job.save(update_fields=['assigned_worker', 'render_settings'])
        assert post_result(client, other.pilot_job.pk, pilot_body(driver_version='32.0.16.1692')).status_code == 200
        assert UnrealMeasurement.objects.get(pk=piece.pk).stale_reason == 'driver_changed'
        assert not {job.pk for job in jobs} & set(poll_ids(client))
