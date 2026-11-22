# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the measurement key of sequence pieces (Unreal
sequence spec D8, FR-SR-1, FR-SW-2, AC-9, AC-32).

Covers: a piece and its pilot share one key, kept by the claim's
``save()`` and rebuilt by the measurement retry (``Target.of``) with the
pilot's frames, resolution and shot set; a resolution changes the key; a
standalone job without one keeps the four-part hash and may set
``unreal.resolution``; manager-only keys and EXR are refused from clients;
a piece's ``render_settings`` cannot be changed; a CVar change makes a new
pilot for the piece key with the submission's frames.
"""

import pytest

from shared.unreal.validation import shot_set_sha256, target_sha256

from workers.models import Job, Project
from workers.services.unreal_retry import retry_measurement

from ..unreal_helpers import BUILD, RENDER_SETTINGS, unreal_asset
from ..unreal_pilot_helpers import JOBS, create_job, created_job, pilots, ready_worker, settings as job_settings
from .sequence_helpers import MAP, PRESET, SEQUENCE
from .submission_helpers import claimed_piece, done_discovery, pieces_of, pilot_of, submit

RESOLUTION = {'resolution_x': 128, 'resolution_y': 72}


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


@pytest.mark.django_db
class TestPieceKey:

    def test_piece_and_pilot_share_the_key(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset, **RESOLUTION)
        pilot = pilot_of(asset)
        expected = target_sha256(BUILD, MAP, SEQUENCE, PRESET, [128, 72],
                                 shot_set_sha256([(0, 4), (4, 10), (20, 24)]))
        assert {p.unreal_target_sha256 for p in pieces_of(submission)} == {expected}
        assert pilot.unreal_target_sha256 == expected
        assert pilot.render_settings['unreal.resolution'] == [128, 72]
        assert pilot.render_settings['unreal.shot_set_sha256'] == submission.shot_set_sha256
        assert pilot.start_frame == 0

    def test_key_survives_claim_and_retry(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, piece = claimed_piece(admin_client, asset, worker, client, **RESOLUTION)
        first_pilot = pilot_of(asset)
        assert piece.status == 'RENDERING'
        assert piece.unreal_target_sha256 == first_pilot.unreal_target_sha256
        record = retry_measurement(Project.objects.get(pk=asset.project_id), job_id=piece.pk, remeasure=True)
        new_pilot = record.pilot_job
        assert new_pilot.pk != first_pilot.pk
        assert record.target_sha256 == new_pilot.unreal_target_sha256 == piece.unreal_target_sha256
        settings_ = new_pilot.render_settings
        assert settings_['unreal.pilot_frames'] == submission.pilot_frames == [0, 4, 20]
        assert settings_['unreal.resolution'] == [128, 72]
        assert settings_['unreal.shot_set_sha256'] == piece.render_settings['unreal.shot_set_sha256']

    def test_resolution_changes_the_key(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        plain, _ = submit(admin_client, asset)
        sized, _ = submit(admin_client, asset, **RESOLUTION)
        assert pieces_of(plain)[0].unreal_target_sha256 != pieces_of(sized)[0].unreal_target_sha256
        assert 'unreal.resolution' not in pieces_of(plain)[0].render_settings
        assert pieces_of(plain)[0].render_settings['unreal.output_size'] == [64, 36]

    def test_cvar_change_makes_a_pilot_with_the_frames(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset)
        before = len(pilots(asset))
        resp = admin_client.patch(f'/api/projects/{asset.project_id}/',
                                  {'unreal_cvar_overrides': {'r.Streaming.PoolSize': '4000'}}, format='json')
        assert resp.status_code == 200, resp.data
        created = pilots(asset)[before:]
        assert len(created) == 1
        assert created[0].render_settings['unreal.pilot_frames'] == submission.pilot_frames
        assert created[0].unreal_target_sha256 == pieces_of(submission)[0].unreal_target_sha256

    def test_piece_render_settings_fixed(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset)
        piece = pieces_of(submission)[0]
        changed = dict(piece.render_settings, **{'unreal.preset': '/Game/Presets/Other.Other'})
        resp = admin_client.patch(f'{JOBS}{piece.pk}/', {'render_settings': changed}, format='json')
        assert resp.status_code == 400
        assert resp.data['render_settings'] == ["A piece job's render_settings cannot be changed."]


@pytest.mark.django_db
class TestStandaloneJobs:

    def test_key_without_resolution_unchanged(self, admin_client, asset):
        job = created_job(admin_client, asset)
        assert job.unreal_target_sha256 == target_sha256(
            BUILD, RENDER_SETTINGS['unreal.map'], RENDER_SETTINGS['unreal.sequence'], RENDER_SETTINGS['unreal.preset'])

    def test_resolution_accepted_and_keyed(self, admin_client, asset, worker_with_token):
        ready_worker(worker_with_token[0])  # per-machine D5: an eligible machine gets the pilot
        job = created_job(admin_client, asset, render_settings=job_settings(resolution=[1280, 720]))
        plain = target_sha256(BUILD, RENDER_SETTINGS['unreal.map'], RENDER_SETTINGS['unreal.sequence'],
                              RENDER_SETTINGS['unreal.preset'])
        assert job.unreal_target_sha256 == target_sha256(
            BUILD, RENDER_SETTINGS['unreal.map'], RENDER_SETTINGS['unreal.sequence'],
            RENDER_SETTINGS['unreal.preset'], [1280, 720])
        assert job.unreal_target_sha256 != plain
        assert pilots(asset)[-1].render_settings['unreal.resolution'] == [1280, 720]

    @pytest.mark.parametrize('value', [[63, 720], [1280], [1280, 720.0], [True, 720], '1280x720'])
    def test_bad_resolution_refused(self, admin_client, asset, value):
        resp = create_job(admin_client, asset, render_settings=job_settings(resolution=value))
        assert resp.status_code == 400
        assert "'unreal.resolution' must be [width, height]" in str(resp.data['render_settings'])

    @pytest.mark.parametrize('key, value', [
        ('unreal.piece_shots', [[1, 0, 4]]), ('unreal.shot_set_sha256', 'a' * 64), ('unreal.output_size', [64, 36]),
        ('unreal.pilot_frames', [0]), ('unreal.draft', {'motion_blur': False}),
    ])
    def test_manager_only_keys_refused_on_create(self, admin_client, asset, key, value):
        resp = create_job(admin_client, asset, render_settings=dict(RENDER_SETTINGS, **{key: value}))
        assert resp.status_code == 400
        assert f"'{key}' is set by sequence renders only." in resp.data['render_settings']
        assert not Job.objects.filter(unreal_kind='render').exists()

    def test_exr_refused_on_create(self, admin_client, asset):
        resp = create_job(admin_client, asset, render_settings=job_settings(output_format='EXR'))
        assert resp.status_code == 400
        assert "EXR output is set by sequence renders only." in resp.data['render_settings']

    def test_manager_only_key_refused_on_patch(self, admin_client, asset):
        job = created_job(admin_client, asset)
        changed = dict(job.render_settings, **{'unreal.piece_shots': [[1, 1000, 1001]]})
        resp = admin_client.patch(f'{JOBS}{job.pk}/', {'render_settings': changed}, format='json')
        assert resp.status_code == 400
        assert "'unreal.piece_shots' is set by sequence renders only." in resp.data['render_settings']
