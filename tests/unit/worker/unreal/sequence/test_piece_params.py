# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A sequence piece's payload and the manager-only render settings on the worker (sequence rendering FR-SW-1,
FR-SW-2, D8, D11): the bounds of ``job_params_sequence``, which keys each job may carry, and the draft CVar tier
(R10: preset < farm < project < draft < farm-critical).
"""
import pytest

from sethlans_worker_agent.unreal_executor import cvars
from sethlans_worker_agent.unreal_executor.job_params import JobDataError, validate_job_data
from tests.unit.worker.unreal._executor_fakes import SETTINGS, job, params
from tests.unit.worker.unreal.sequence._pieces import SHA, piece_data, piece_params


def _field(data):
    with pytest.raises(JobDataError) as caught:
        validate_job_data(data)
    return caught.value.field


class TestPiece:

    def test_a_valid_piece(self):
        p = piece_params(shots=[[3, 100, 110], [4, 110, 130]], fmt='EXR')
        assert p.is_piece and p.kind == 'render' and p.frame is None and p.output_format == 'EXR'
        piece = p.piece
        assert (piece.start, piece.end, piece.lead, piece.first, piece.rendered) == (100, 129, 30, 70, 60)
        assert piece.shots == ((3, 100, 110), (4, 110, 130)) and piece.output_size == (1920, 1080)
        assert p.seq.shot_set_sha256 == SHA and [r.peak_mb for r in p.machine_results] == [7930]

    def test_a_one_frame_piece_at_frame_0_without_a_lead_in(self):
        p = piece_params(start=0, end=0, lead=0, size=None)
        assert (p.piece.first, p.piece.rendered, p.piece.output_size) == (0, 1, None)

    @pytest.mark.parametrize('changes,field', [
        ({'start': 10, 'end': 9, 'shots': [[1, 10, 11]]}, 'end_frame'),
        ({'start': -1, 'end': 5, 'lead': 0, 'shots': [[1, -1, 6]]}, 'end_frame'),
        ({'lead': 101}, 'unreal_lead_in_frames'),
        ({'lead': -1}, 'unreal_lead_in_frames'),
        ({'lead': None}, 'unreal_lead_in_frames'),
        ({'lead': True}, 'unreal_lead_in_frames'),
        ({'start': 10, 'end': 200_009, 'lead': 1, 'shots': [[1, 10, 200_010]]}, 'end_frame'),
        ({'sequence_submission': 0}, 'sequence_submission'),
        ({'sequence_submission': 'x'}, 'sequence_submission'),
    ])
    def test_frame_rules(self, changes, field):
        assert _field(piece_data(**changes)) == field

    def test_200000_rendered_frames_is_the_limit(self):
        p = piece_params(start=10, end=200_008, lead=1, shots=[[1, 10, 200_009]])
        assert p.piece.rendered == 200_000

    @pytest.mark.parametrize('shots', [
        [], [[1, 100, 120]], [[1, 100, 120], [2, 121, 130]], [[1, 100, 120], [2, 119, 130]],
        [[2, 100, 120], [1, 120, 130]], [[1, 100, 120], [1, 120, 130]], [[1, 99, 130]], [[1, 100, 131]],
        [[0, 100, 130]], [[2001, 100, 130]], [[1, 100, 100], [2, 100, 130]], [[1, 100, '130']], [[1, 100]],
        'x', [[1, 100, 101]] * 2001,
    ])
    def test_piece_shots_must_cover_the_kept_frames_in_order(self, shots):
        assert _field(piece_data(shots=shots)) == 'render_settings.unreal.piece_shots'

    @pytest.mark.parametrize('key,value', [
        ('unreal.output_size', [0, 1080]), ('unreal.output_size', [1920]), ('unreal.output_size', [1920, True]),
        ('unreal.output_size', [16385, 1080]), ('unreal.resolution', [63, 64]), ('unreal.resolution', [64, 16385]),
        ('unreal.resolution', '1920x1080'), ('unreal.shot_set_sha256', 'C' * 64), ('unreal.shot_set_sha256', 'c' * 63),
        ('unreal.draft', {'temporal_samples': 0}), ('unreal.draft', {'spatial_samples': 1025}),
        ('unreal.draft', {'screen_percentage': 9}), ('unreal.draft', {'screen_percentage': 101}),
        ('unreal.draft', {'motion_blur': True}), ('unreal.draft', {'ray_traced_shadows': 0}),
        ('unreal.draft', {'resolution': 50}), ('unreal.draft', ['motion_blur']),
    ])
    def test_setting_bounds(self, key, value):
        data = piece_data()
        data['render_settings'][key] = value
        assert _field(data).startswith(f'render_settings.{key}')

    def test_the_draft_tier_sits_between_the_project_and_the_farm_critical_tier(self):
        p = piece_params(draft={'temporal_samples': 2, 'screen_percentage': 50, 'motion_blur': False,
                                'ray_traced_shadows': False, 'ray_traced_reflections': False})
        assert dict(p.seq.draft) == {'temporal_samples': 2, 'screen_percentage': 50, 'motion_blur': False,
                                     'ray_traced_shadows': False, 'ray_traced_reflections': False}
        assert p.seq.draft_cvars == (('r.ScreenPercentage', '50'), ('r.RayTracing.Shadows', '0'),
                                     ('r.Lumen.Reflections.HardwareRayTracing', '0'), ('r.MotionBlurQuality', '0'))
        merged = dict(p.merged)
        assert merged['r.ScreenPercentage'] == '50' and merged['r.Streaming.PoolSize'] == '9000'
        assert set(dict(p.seq.draft_cvars)) <= set(cvars.DRAFT_CVARS.values())
        merged = cvars.merge(overrides={'r.ScreenPercentage': '70'}, draft={'r.screenpercentage': '50'})
        assert merged[-1] == ('r.ScreenPercentage', '50')
        assert cvars.merge(draft={'r.A': '1'}, critical={'r.a': '2'})[-1] == ('r.A', '2')

    @pytest.mark.parametrize('project,draft,sent', [
        ('50', 80, '50'), ('50', 100, '50'), ('50', 50, '50'), ('80', 30, '30'), ('abc', 30, 'abc'),
    ])
    def test_a_draft_never_raises_a_project_value(self, project, draft, sent):
        """Review B1: a project r.ScreenPercentage of 50 with a draft of 80 renders at 50, not 80."""
        data = piece_data(draft={'screen_percentage': draft})
        data['asset']['project_details']['unreal_cvar_overrides']['r.screenpercentage'] = project
        p = validate_job_data(data)
        assert {name.casefold(): value for name, value in p.merged}['r.screenpercentage'] == sent
        from sethlans_worker_agent.unreal_executor import sequence_prep
        assert sequence_prep.sent_draft_cvars(p) == ((('r.ScreenPercentage', sent),) if sent == str(draft) else ())

    @pytest.mark.parametrize('below,kept', [
        ({}, {'r.RayTracing.Shadows': '0'}), ({'r.raytracing.shadows': '1'}, {'r.RayTracing.Shadows': '0'}),
        ({'r.raytracing.shadows': '0'}, {}), ({'r.raytracing.shadows': 'false'}, {}),
        ({'r.raytracing.shadows': 'True'}, {'r.RayTracing.Shadows': '0'}),
    ])
    def test_the_draft_tier_keeps_only_values_that_lower(self, below, kept):
        assert cvars.draft_tier({'r.RayTracing.Shadows': '0'}, below) == kept

    def test_samples_are_not_cvars_and_motion_blur_off_sends_its_post_process_cvar(self):
        assert piece_params(draft={'temporal_samples': 1}).seq.draft_cvars == ()
        p = piece_params(draft={'temporal_samples': 1, 'motion_blur': False})
        assert p.seq.draft_cvars == (('r.MotionBlurQuality', '0'),) and dict(p.merged)['r.MotionBlurQuality'] == '0'

    @pytest.mark.parametrize('project,merged', [(None, '0'), ('2', '0'), ('0', '0'), ('-1', '-1')])
    def test_motion_blur_off_never_raises_a_project_value(self, project, merged):
        data = piece_data(draft={'motion_blur': False})
        if project is not None:
            data['asset']['project_details']['unreal_cvar_overrides']['r.MotionBlurQuality'] = project
        assert dict(validate_job_data(data).merged)['r.MotionBlurQuality'] == merged

    def test_the_warm_up_key_leaves_out_shader_neutral_cvars_only(self):
        """A draft's screen percentage or motion blur off reuses the full render's warm-up; ray tracing does not."""
        full = piece_params()
        key = cvars.warmup_cvars_sha256
        assert key(piece_params(draft={'screen_percentage': 50, 'motion_blur': False}).merged) == key(full.merged)
        assert key(piece_params(draft={'ray_traced_shadows': False}).merged) != key(full.merged)
        assert key(piece_params(draft={'ray_traced_reflections': False}).merged) != key(full.merged)
        assert cvars.cvars_sha256(piece_params(draft={'screen_percentage': 50}).merged) != cvars.cvars_sha256(full.merged)


class TestWhoMayCarryWhat:

    @pytest.mark.parametrize('key,value', [
        ('unreal.piece_shots', [[1, 1000, 1001]]), ('unreal.output_size', [64, 64]), ('unreal.pilot_frames', [1000]),
        ('unreal.draft', {}), ('unreal.shot_set_sha256', SHA),
    ])
    def test_a_standalone_render_job_carries_only_a_resolution(self, key, value):
        assert _field(job(render_settings=dict(SETTINGS, **{key: value}))) == 'render_settings'

    def test_a_standalone_render_job_may_set_a_resolution_and_stays_one_frame(self):
        p = params(render_settings=dict(SETTINGS, **{'unreal.resolution': [1280, 720]}))
        assert p.seq.resolution == (1280, 720) and not p.is_piece and p.frame == 1000
        assert _field(job(render_settings=dict(SETTINGS, **{'unreal.output_format': 'EXR'}))) == \
            'render_settings.unreal.output_format'
        assert _field(job(unreal_lead_in_frames=5)) == 'unreal_lead_in_frames'

    def test_a_sequence_pilot_carries_its_frames_resolution_and_shot_set(self):
        settings = dict(SETTINGS, **{'unreal.pilot_frames': [0, 25, 30], 'unreal.resolution': [64, 64],
                                     'unreal.shot_set_sha256': SHA})
        p = params(kind='pilot', render_settings=settings)
        assert p.seq.pilot_frames == (0, 25, 30) and p.seq.resolution == (64, 64) and not p.is_piece

    @pytest.mark.parametrize('frames', [[], [5, 5], [6, 5], [-1], list(range(257)), [True]])
    def test_pilot_frame_bounds(self, frames):
        data = job(kind='pilot', render_settings=dict(SETTINGS, **{'unreal.pilot_frames': frames}))
        assert _field(data) == 'render_settings.unreal.pilot_frames'

    @pytest.mark.parametrize('key,value', [('unreal.output_size', [64, 64]), ('unreal.piece_shots', [[1, 0, 1]])])
    def test_a_pilot_never_carries_piece_keys(self, key, value):
        assert _field(job(kind='pilot', render_settings=dict(SETTINGS, **{key: value}))) == 'render_settings'

    def test_a_shots_job_carries_no_sequence_key(self):
        data = job(kind='shots', frame=0, render_settings=dict(SETTINGS, **{'unreal.resolution': [64, 64]}))
        assert _field(data) == 'render_settings'
