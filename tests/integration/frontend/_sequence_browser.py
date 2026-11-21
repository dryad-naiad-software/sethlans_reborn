# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Seed rows for the sequence versions browser test (Unreal sequence chunk 8).

One Unreal project with one version written straight to the database, no
render: a DONE discovery with two shots, a RENDERING version whose shot 1
is DONE (its piece DONE) and shot 2 ERROR (its piece ERROR), and the
version's audio job still QUEUED, so the version can be canceled.  The
jobs are written with ``bulk_create`` (as ``services/sequence_create.py``
does), so no signal refreshes the version before the test acts; the
cancel's own refresh then derives every state from these rows.

Shot 2's name is hostile markup.  The worker and the manager clean shot
names on the way in (FR-SD-6); writing the row directly skips that, so the
page itself must show the name as text.

Module name starts with an underscore so pytest does not collect it.
"""

from dataclasses import dataclass

from workers.constants import Renderer, RenderDevice, UnrealKind
from workers.constants_sequence import PIECE_SHOTS_KEY
from workers.models import Job, SequenceDiscovery, SequenceShot, SequenceSubmission
from workers.renderers.unreal_settings import MAP, PRESET, SEQUENCE

from tests.integration.manager.unreal_helpers import BUILD, unreal_asset, unreal_project

MAP_PATH = '/Game/Maps/Harbor.Harbor'
SEQUENCE_PATH = '/Game/Cinematics/Ep01.Ep01'
PRESET_PATH = '/Game/Presets/Farm.Farm'
VERSION_NAME = 'Ep01 v1'
DONE_SHOT = 'sh010'
HOSTILE_SHOT = '<b>x</b>'
SHOT_ERROR = 'GPU lost'
FACTS = {'resolution': [64, 36], 'temporal_samples': 1, 'spatial_samples': 1, 'can_png': True, 'can_exr': True,
         'can_sound': True}


@dataclass
class SeededVersion:
    project_id: str
    submission: SequenceSubmission
    audio: Job


def _job(submission, kind, start, end, status, shots, error=''):
    """A piece or audio job of ``submission`` over frames ``[start, end]`` (inclusive, as on a Job)."""
    name = f'seq-{submission.pk}-{kind}-{start}'
    job = Job(
        name=name, asset_id=submission.asset_id, output_file_pattern=f'//sethlans_seq/{name}.png',
        renderer=Renderer.UNREAL, engine_build=submission.engine_build, unreal_kind=kind,
        render_device=RenderDevice.GPU, start_frame=start, end_frame=end, sequence_submission_id=submission.pk,
        status=status, error_message=error, render_settings={
            MAP: submission.map, SEQUENCE: submission.sequence, PRESET: submission.preset, PIECE_SHOTS_KEY: shots,
        },
    )
    job.unreal_target_sha256 = job.compute_target_sha256()
    return job


def seed_mixed_version():
    """The project, discovery, version, shots and jobs; returns a :class:`SeededVersion`."""
    asset = unreal_asset(unreal_project(name='Harbor Episode'))
    key = dict(project=asset.project, asset=asset, engine_build=BUILD, map=MAP_PATH, sequence=SEQUENCE_PATH,
               preset=PRESET_PATH)
    discovery = SequenceDiscovery.objects.create(
        **key, status='DONE', playback_start=0, playback_end=10, frame_rate_num=30, frame_rate_den=1,
        shot_source='shot_tracks', preset_facts=FACTS,
        shots=[{'index': 1, 'name': DONE_SHOT, 'start': 0, 'end': 4},
               {'index': 2, 'name': HOSTILE_SHOT, 'start': 4, 'end': 10}],
    )
    submission = SequenceSubmission.objects.create(
        **key, discovery=discovery, version=1, name=VERSION_NAME, piece_seconds=30, lead_in_ms=0,
        status='RENDERING', sound=True,
    )
    SequenceShot.objects.bulk_create([
        SequenceShot(submission=submission, index=1, name=DONE_SHOT, start_frame=0, end_frame=4, status='DONE'),
        SequenceShot(submission=submission, index=2, name=HOSTILE_SHOT, start_frame=4, end_frame=10,
                     status='ERROR', error_message=SHOT_ERROR),
    ])
    audio = _job(submission, UnrealKind.AUDIO, 0, 9, 'QUEUED', [[1, 0, 4], [2, 4, 10]])
    Job.objects.bulk_create([
        _job(submission, UnrealKind.RENDER, 0, 3, 'DONE', [[1, 0, 4]]),
        _job(submission, UnrealKind.RENDER, 4, 9, 'ERROR', [[2, 4, 10]], error=SHOT_ERROR),
        audio,
    ])
    return SeededVersion(project_id=str(asset.project_id), submission=submission, audio=audio)
