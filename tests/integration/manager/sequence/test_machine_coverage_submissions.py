# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: coverage and the heartbeat sweep for real sequence
submissions (Unreal per-machine memory test AC-5, D8, D10, D16,
FR-MT-P-2, FR-MT-P-3, FR-MT-P-4).

A full submission's pilot (all three shots) is measured ``fits`` the way
``pilot-result/`` leaves it.  Covers: a later submission of a subset of
its shots, or a draft of the same shots, plans no pilot on that machine:
it gets a derived record and its pieces are offered to the machine; a
subset with a shot the full pilot did not render (a sampled pilot) gets a
pilot; the admin retry of a covered key creates a real pilot, never a
derived record; a key whose only QUEUED jobs are audio jobs is found by
the sweep through the submission's piece key and gets a pilot for a
machine that comes online.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from workers.constants import WORKER_STALENESS_SECONDS
from workers.models import Job, UnrealMeasurement, Worker

from ..machine_test_helpers import heartbeat
from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import KIND, measure, pilots, ready_worker, record_of
from .sequence_helpers import poll_ids
from .sound_helpers import audio_jobs_of
from .submission_helpers import done_discovery, pieces_of, submit

DRAFT = {'temporal_samples': 2, 'motion_blur': False}


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.fixture
def full(admin_client, asset, worker_with_token):
    """The full submission, its machine's pilot record measured ``fits``: ``(worker, client, submission, R)``."""
    worker, client = worker_with_token
    done_discovery(admin_client, asset, worker, client)
    submission, _ = submit(admin_client, asset)
    submission.refresh_from_db()
    [pilot] = pilots(asset)
    record = record_of(pilot)
    assert record.measured_ranges == [[0, 4], [4, 10], [20, 24]]
    measure(record, peak_mb=9100)
    UnrealMeasurement.objects.filter(pk=record.pk).update(driver_version='32.0.15.6094')
    return worker, client, submission, UnrealMeasurement.objects.get(pk=record.pk)


def _key_pilots(asset, submission):
    submission.refresh_from_db()
    return [p for p in pilots(asset) if p.unreal_target_sha256 == submission.piece_target_sha256]


def _derived(submission, worker):
    return UnrealMeasurement.objects.get(target_sha256=submission.piece_target_sha256, worker=worker,
                                         card_kind=KIND, current=True, verdict_source='covered')


@pytest.mark.django_db
class TestCoveredSubmissions:

    @pytest.mark.parametrize('overrides', [{'shots': ['sh010']}, {'draft': dict(DRAFT)}], ids=['subset', 'draft'])
    def test_no_pilot_and_the_pieces_are_offered(self, admin_client, asset, full, overrides):
        worker, client, _full_submission, record = full
        covered, _ = submit(admin_client, asset, **overrides)
        assert _key_pilots(asset, covered) == []
        derived = _derived(covered, worker)
        assert (derived.covered_by_id, derived.pilot_job_id, derived.peak_mb) == (record.pk, None, 9100)
        assert derived.driver_version == '32.0.15.6094'
        assert {p.pk for p in pieces_of(covered)} <= set(poll_ids(client))

    def test_a_shot_the_pilot_did_not_render_needs_a_pilot(self, admin_client, asset, full):
        worker, _client, _full_submission, record = full
        # As a sampled pilot leaves it: only the ranges whose start frame it rendered.
        UnrealMeasurement.objects.filter(pk=record.pk).update(measured_ranges=[[0, 4], [20, 24]])
        partial, _ = submit(admin_client, asset, shots=['sh020'])
        [pilot] = _key_pilots(asset, partial)
        assert (pilot.target_worker_id, pilot.render_settings['unreal.card_kind']) == (worker.pk, KIND)
        assert not UnrealMeasurement.objects.filter(target_sha256=partial.piece_target_sha256,
                                                    verdict_source='covered').exists()

    def test_the_retry_of_a_covered_key_makes_a_real_pilot(self, admin_client, asset, full):
        worker, _client, _full_submission, _record = full
        covered, _ = submit(admin_client, asset, shots=['sh010'])
        derived = _derived(covered, worker)
        resp = admin_client.post(f'/api/projects/{asset.project_id}/unreal-measurement/retry/',
                                 {'job_id': pieces_of(covered)[0].pk, 'remeasure': True}, format='json')
        assert resp.status_code == 201, resp.data
        new = UnrealMeasurement.objects.get(pk=resp.data['measurement_id'])
        assert (new.pilot_job_id, new.verdict_source, new.remeasure, new.current) == (
            resp.data['pilot_job_id'], '', True, True)
        assert UnrealMeasurement.objects.get(pk=derived.pk).current is False


@pytest.mark.django_db
def test_an_audio_only_key_is_swept_by_its_piece_key(admin_client, asset, worker_with_token, make_worker_client,
                                                     django_capture_on_commit_callbacks):
    worker, client = worker_with_token
    done_discovery(admin_client, asset, worker, client)
    late, late_client, _token = make_worker_client('mt-sweep-audio')
    ready_worker(late)
    Worker.objects.filter(pk=late.pk).update(
        last_seen=timezone.now() - timedelta(seconds=WORKER_STALENESS_SECONDS + 60))
    submission, _ = submit(admin_client, asset, sound=True)
    assert [p.target_worker_id for p in _key_pilots(asset, submission)] == [worker.pk]
    Job.objects.filter(pk__in=[p.pk for p in pieces_of(submission)]).update(status='DONE')
    assert {j.status for j in audio_jobs_of(submission)} == {'QUEUED'}
    heartbeat(late_client, late, django_capture_on_commit_callbacks)
    assert sorted(p.target_worker_id for p in _key_pilots(asset, submission)) == sorted([worker.pk, late.pk])
