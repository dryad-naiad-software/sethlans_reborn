# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the job, once-per-job, card-kind and record checks of
``memory-report/`` and their order (Unreal per-machine memory test
FR-MT-S-3, D10, FR-MT-R-2).

Order as built: 409 when the job is not RENDERING on the caller; 409 when
it is not an Unreal render or audio job (Blender, pilot, shots); once per
job (an identical repeat answers 200 with the stored answer even after
the pair changed, a different body 409, before any 400 check); 400 when
``card_kind`` is not the kind of the caller's stored card ``card_key``
(an un-ticked card keeps its kind); 400 when the record is not the
caller's, of that kind, of the job's asset and target (a record of the
same target under other CVars is accepted).  An audio job's record is
of its submission's piece key.  Refusals store nothing.  A requeued and
re-claimed job may report again (its report was cleared).
"""

import pytest
from django.core.files.base import ContentFile

from shared.unreal.validation import target_sha256

from workers.models import Asset, Job, UnrealMeasurement

from .machine_test_helpers import KIND_AMD, kind_card, kinds_worker, pilot_job, render_job, set_cards
from .memory_report_helpers import (
    ALREADY, KIND_MISMATCH, NOT_KEYED, NOT_RENDERING, NOT_THE_RECORD, accepted, keyed_record, post_report,
    rendering_render, report_body, self_spill, state, stored_report,
)
from .sequence.sequence_helpers import MAP, PRESET, SEQUENCE
from .sequence.sound_helpers import claimed_audio
from .unreal_helpers import BUILD, card_key_of, unreal_asset
from .unreal_pilot_helpers import JOBS, KIND, claim, ready_worker


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def caller(worker_with_token):
    worker, client = worker_with_token
    return kinds_worker(worker, KIND, KIND_AMD), client


@pytest.fixture
def rendering(asset, caller):
    worker, client = caller
    job, record = rendering_render(asset, worker, client)
    return job, record, worker, client


def _refused(client, job, body, code, data):
    resp = post_report(client, job, body)
    assert (resp.status_code, resp.data) == (code, data), resp.data
    assert stored_report(job) is None


@pytest.mark.django_db
class TestJobChecks:

    @pytest.mark.parametrize('status', ['QUEUED', 'DONE', 'ERROR', 'CANCELED'])
    def test_not_rendering_is_409(self, rendering, status):
        job, record, _worker, client = rendering
        Job.objects.filter(pk=job.pk).update(status=status)
        _refused(client, job, report_body(record), 409, {'error': NOT_RENDERING})

    def test_another_workers_job_is_409(self, rendering, make_worker_client):
        job, record, _worker, _client = rendering
        other, other_client, _token = make_worker_client('mt-report-other')
        kinds_worker(other, KIND)
        _refused(other_client, job, report_body(record), 409, {'error': NOT_RENDERING})

    def test_not_rendering_wins_over_a_wrong_record(self, asset, caller):
        worker, client = caller
        job = render_job(asset)
        _refused(client, job, report_body(keyed_record(asset, 'f1' * 32, worker), measurement_id=10 ** 9),
                 409, {'error': NOT_RENDERING})

    def test_a_blender_job_is_409(self, rendering, project):
        _job, record, worker, client = rendering
        blend = Asset.objects.create(project=project, name='MtBlend',
                                     blend_file=ContentFile(b'BLENDER' + b'\x00' * 57, name='b.blend'))
        job = Job.objects.create(name='MtBlenderJob', asset=blend, output_file_pattern='//r/f.png',
                                 status='RENDERING', assigned_worker=worker)
        assert job.renderer == 'BLENDER'
        _refused(client, job, report_body(record), 409, {'error': NOT_KEYED})

    @pytest.mark.parametrize('kind', ['pilot', 'shots'])
    def test_a_pilot_or_shots_job_is_409(self, rendering, asset, kind):
        _job, record, worker, client = rendering
        job = pilot_job(asset, worker, status='RENDERING')
        Job.objects.filter(pk=job.pk).update(unreal_kind=kind, assigned_worker=worker)
        _refused(client, job, report_body(record), 409, {'error': NOT_KEYED})


@pytest.mark.django_db
class TestOncePerJob:

    def test_an_identical_repeat_gets_the_stored_answer(self, rendering):
        job, record, _worker, client = rendering
        body = report_body(record, job_peak_mb=9000)
        accepted(client, job, body)
        # The pair changed meanwhile: the repeat still gets the answer stored with the report.
        UnrealMeasurement.objects.filter(pk=record.pk).update(verdict='does_not_fit')
        before = state(record)
        accepted(client, job, body)
        assert state(record) == before
        assert stored_report(job) == {'report': body, 'result': 'fits'}

    @pytest.mark.parametrize('change', [{'job_peak_mb': 4001}, {'spilled': None}, {'driver_version': None},
                                        {'card_kind': KIND_AMD, 'card_key': card_key_of(1)}])
    def test_a_different_body_is_409(self, rendering, change):
        job, record, _worker, client = rendering
        body = report_body(record)
        accepted(client, job, body)
        resp = post_report(client, job, {**body, **change})
        assert (resp.status_code, resp.data) == (409, {'error': ALREADY})
        assert stored_report(job)['report'] == body

    def test_a_repeat_after_the_job_ended_is_409_not_rendering(self, rendering):
        job, record, _worker, client = rendering
        accepted(client, job, report_body(record))
        Job.objects.filter(pk=job.pk).update(status='DONE')
        resp = post_report(client, job, report_body(record))
        assert (resp.status_code, resp.data) == (409, {'error': NOT_RENDERING})

    def test_a_requeued_and_reclaimed_job_reports_again(self, rendering):
        job, record, worker, client = rendering
        accepted(client, job, report_body(record))
        resp = client.post(f'{JOBS}{job.pk}/yield-requeue/', {'reason': 'shutdown'}, format='json')
        assert (resp.status_code, resp.data) == (200, {'status': 'requeued'})
        assert stored_report(job) is None
        assert claim(client, job, worker).status_code == 200
        second = report_body(record, job_peak_mb=9100)
        accepted(client, job, second)
        assert stored_report(job) == {'report': second, 'result': 'fits'}
        assert state(record)[3] == 9100


@pytest.mark.django_db
class TestCardKind:

    @pytest.mark.parametrize('card_key', [card_key_of(1), card_key_of(7)], ids=['other-kind', 'not-stored'])
    def test_not_the_cards_kind_is_400(self, rendering, card_key):
        job, record, _worker, client = rendering
        _refused(client, job, report_body(record, card_key=card_key), 400, {'card_kind': [KIND_MISMATCH]})

    def test_an_unticked_card_keeps_its_kind(self, rendering):
        job, record, worker, client = rendering
        set_cards(worker, [kind_card(KIND, 0, ticked=False), kind_card(KIND_AMD, 1)])
        accepted(client, job, report_body(record))

    def test_both_errors_together(self, rendering):
        job, record, _worker, client = rendering
        _refused(client, job, report_body(record, card_key=card_key_of(7), measurement_id=10 ** 9), 400,
                 {'card_kind': [KIND_MISMATCH], 'measurement_id': [NOT_THE_RECORD]})


@pytest.mark.django_db
class TestRecord:

    def _wrong(self, rendering, record_id, **body):
        job, record, _worker, client = rendering
        before = state(record)
        _refused(client, job, report_body(record, measurement_id=record_id, **body), 400,
                 {'measurement_id': [NOT_THE_RECORD]})
        assert state(record) == before

    def test_a_missing_record(self, rendering):
        self._wrong(rendering, 10 ** 9)

    def test_another_workers_record(self, rendering, asset, make_worker_client):
        job, _record, _worker, _client = rendering
        other, _other_client, _token = make_worker_client('mt-report-rec')
        ready_worker(other)
        self._wrong(rendering, keyed_record(asset, job.unreal_target_sha256, other).pk)

    def test_a_record_of_another_kind(self, rendering, asset):
        job, _record, worker, _client = rendering
        self._wrong(rendering, keyed_record(asset, job.unreal_target_sha256, worker, KIND_AMD).pk)

    def test_a_record_of_another_target(self, rendering, asset):
        _job, _record, worker, _client = rendering
        self._wrong(rendering, keyed_record(asset, 'f2' * 32, worker).pk)

    def test_a_record_of_another_asset(self, rendering, asset):
        job, _record, worker, _client = rendering
        other = unreal_asset(asset.project, name='MtReportOther')
        self._wrong(rendering, keyed_record(other, job.unreal_target_sha256, worker).pk)

    def test_the_same_target_under_other_cvars_is_accepted(self, rendering, asset):
        job, _record, worker, client = rendering
        changed = keyed_record(asset, job.unreal_target_sha256, worker, overrides='ff' * 32)
        accepted(client, job, report_body(changed))


@pytest.mark.django_db
class TestAudioPieceKey:

    @pytest.fixture
    def audio(self, admin_client, worker_with_token, settings, tmp_path):
        settings.MEDIA_ROOT = str(tmp_path / 'media')
        worker, client = worker_with_token
        asset = unreal_asset()
        submission, job = claimed_audio(admin_client, asset, worker, client)
        piece = UnrealMeasurement.objects.get(target_sha256=submission.piece_target_sha256, worker=worker,
                                              current=True)
        return asset, job, piece, worker, client

    def test_the_piece_keys_record_is_accepted(self, audio):
        _asset, job, piece, _worker, client = audio
        accepted(client, job, self_spill(piece), result='does_not_fit')
        assert state(piece)[:2] == ('does_not_fit', 'spill')

    def test_a_record_of_the_standalone_key_is_400(self, audio):
        asset, job, _piece, worker, client = audio
        standalone = keyed_record(asset, target_sha256(BUILD, MAP, SEQUENCE, PRESET), worker)
        _refused(client, job, report_body(standalone), 400, {'measurement_id': [NOT_THE_RECORD]})
