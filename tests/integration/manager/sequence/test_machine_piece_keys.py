# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: draft values in the key and the audio job's piece key
under per-machine results (Unreal per-machine memory test OQ-1, D8, D10,
FR-MT-R-2, FR-MT-R-3, FR-MT-T-1, FR-MT-T-2).

Covers: a draft changes the pieces' key (stored, recomputed by
``Job.compute_target_sha256``, written to ``piece_target_sha256`` and
carried by each machine's pilot with ``unreal.draft``) while the base key
stays that of the full settings; the draft hash ignores key order; a
render-settings save re-hashes with the draft; the pilot record keeps
the measured ranges of its rendered frames; an audio job is offered and
claimed by its submission's piece key, per worker, with the pair-state
messages; a client cannot send ``unreal.card_kind``.
"""

import pytest

from shared.unreal.validation import target_sha256

from workers.models import Job, UnrealMeasurement

from ..machine_test_helpers import DOES_NOT_FIT, NOT_TESTED
from ..unreal_helpers import BUILD, RENDER_SETTINGS, unreal_asset
from ..unreal_pilot_helpers import KIND, claim, create_job, fits_record, pilots, ready_worker
from .sequence_helpers import MAP, PRESET, SEQUENCE, poll_ids
from .sound_helpers import audio_jobs_of
from .submission_helpers import done_discovery, pieces_of, submit

DRAFT = {'temporal_samples': 2, 'motion_blur': False}


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.fixture
def discovered(admin_client, asset, worker_with_token):
    worker, client = worker_with_token
    done_discovery(admin_client, asset, worker, client)
    return worker, client


@pytest.mark.django_db
class TestDraftKey:

    def test_a_draft_has_its_own_piece_key(self, admin_client, asset, discovered):
        worker, _client = discovered
        plain, _ = submit(admin_client, asset)
        drafted, _ = submit(admin_client, asset, draft=dict(DRAFT))
        plain.refresh_from_db()
        drafted.refresh_from_db()
        assert drafted.shot_set_sha256 == plain.shot_set_sha256
        expected = target_sha256(BUILD, MAP, SEQUENCE, PRESET, None, drafted.shot_set_sha256, DRAFT)
        assert {p.unreal_target_sha256 for p in pieces_of(drafted)} == {expected}
        assert {p.compute_target_sha256() for p in pieces_of(drafted)} == {expected}
        assert drafted.piece_target_sha256 == expected != plain.piece_target_sha256
        assert plain.piece_target_sha256 == pieces_of(plain)[0].unreal_target_sha256
        [pilot] = [p for p in pilots(asset) if p.unreal_target_sha256 == expected]
        assert (pilot.target_worker_id, pilot.render_settings['unreal.card_kind']) == (worker.pk, KIND)
        assert pilot.render_settings['unreal.draft'] == DRAFT
        record = UnrealMeasurement.objects.get(pilot_job=pilot)
        plain_record = UnrealMeasurement.objects.get(target_sha256=plain.piece_target_sha256)
        assert record.base_target_sha256 == plain_record.base_target_sha256
        assert record.base_target_sha256 == target_sha256(BUILD, MAP, SEQUENCE, PRESET, None)
        assert record.measured_ranges == [[0, 4], [4, 10], [20, 24]] == drafted.measured_ranges

    def test_the_draft_hash_ignores_key_order(self, asset):
        job = Job(asset=asset, renderer='UNREAL', engine_build=BUILD,
                  render_settings={**RENDER_SETTINGS, 'unreal.draft': {'motion_blur': False, 'temporal_samples': 2}})
        reordered = Job(asset=asset, renderer='UNREAL', engine_build=BUILD,
                        render_settings={**RENDER_SETTINGS, 'unreal.draft': {'temporal_samples': 2, 'motion_blur': False}})
        plain = Job(asset=asset, renderer='UNREAL', engine_build=BUILD, render_settings=dict(RENDER_SETTINGS))
        assert job.compute_target_sha256() == reordered.compute_target_sha256() != plain.compute_target_sha256()
        assert plain.compute_target_sha256() == target_sha256(
            BUILD, RENDER_SETTINGS['unreal.map'], RENDER_SETTINGS['unreal.sequence'], RENDER_SETTINGS['unreal.preset'])

    def test_a_settings_save_rehashes_with_the_draft(self, asset):
        job = Job.objects.create(name='MtDraftSave', asset=asset, output_file_pattern='//r/f.png',
                                 render_settings=dict(RENDER_SETTINGS))
        before = job.unreal_target_sha256
        job.render_settings = {**RENDER_SETTINGS, 'unreal.draft': dict(DRAFT)}
        job.save(update_fields=['render_settings'])
        job.refresh_from_db()
        assert job.unreal_target_sha256 != before
        assert job.unreal_target_sha256 == job.compute_target_sha256()

    def test_a_client_cannot_send_a_card_kind(self, admin_client, asset):
        resp = create_job(admin_client, asset, render_settings={**RENDER_SETTINGS, 'unreal.card_kind': KIND})
        assert resp.status_code == 400
        assert "'unreal.card_kind' is set by sequence renders only." in resp.data['render_settings']


@pytest.mark.django_db
class TestAudioByPieceKey:

    @pytest.fixture
    def audio(self, admin_client, asset, discovered, make_worker_client):
        worker_a, client_a = discovered
        worker_b, client_b, _token = make_worker_client('mt-audio-b')
        ready_worker(worker_b)
        submission, _ = submit(admin_client, asset, sound=True)
        submission.refresh_from_db()
        return submission, audio_jobs_of(submission), (worker_a, client_a), (worker_b, client_b)

    def test_offered_only_to_the_worker_with_a_fits_piece_key(self, asset, audio):
        submission, jobs, (worker_a, client_a), (worker_b, client_b) = audio
        ids = {job.pk for job in jobs}
        fits_record(asset, submission.piece_target_sha256, worker_a)
        # A fits result of another key (the standalone one) does not open the audio jobs to worker b.
        fits_record(asset, target_sha256(BUILD, MAP, SEQUENCE, PRESET), worker_b)
        assert ids <= set(poll_ids(client_a))
        assert not ids & set(poll_ids(client_b))
        assert claim(client_b, jobs[0], worker_b).data == {'error': NOT_TESTED}
        resp = claim(client_a, jobs[0], worker_a)
        assert resp.status_code == 200, resp.data
        assert resp.data['unreal_machine_results'][0]['card_kind'] == KIND

    def test_does_not_fit_message_by_the_piece_key(self, asset, audio):
        submission, jobs, _first, (worker_b, client_b) = audio
        # Worker b's own pilot of the piece key finished: does not fit (its record stays the current one).
        record = UnrealMeasurement.objects.get(target_sha256=submission.piece_target_sha256, worker=worker_b,
                                               current=True)
        UnrealMeasurement.objects.filter(pk=record.pk).update(verdict='does_not_fit', peak_mb=30000)
        Job.objects.filter(pk=record.pilot_job_id).update(status='DONE')
        assert claim(client_b, jobs[0], worker_b).data == {'error': DOES_NOT_FIT}
        assert not {job.pk for job in jobs} & set(poll_ids(client_b))

    def test_each_worker_got_its_own_pilot_of_the_piece_key(self, asset, audio):
        submission, _jobs, (worker_a, _client_a), (worker_b, _client_b) = audio
        found = [p for p in pilots(asset) if p.unreal_target_sha256 == submission.piece_target_sha256]
        assert sorted(p.target_worker_id for p in found) == sorted([worker_a.pk, worker_b.pk])
