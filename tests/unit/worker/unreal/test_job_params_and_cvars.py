# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""FR-UE-1 check 2 (``validate_job_data``), FR-UE-5 (CVar merge) and the FR-UE-8 effective-CVar check."""
import pytest

from sethlans_worker_agent.unreal_executor import cvars
from sethlans_worker_agent.unreal_executor.job_params import JobDataError, validate_job_data
from tests.unit.worker.unreal._executor_fakes import OVERRIDES, job


class TestValidateJobData:

    def test_normal_job(self):
        p = validate_job_data(job())
        assert (p.job_id, p.kind, p.frame, p.measured_peak_mb) == (42, 'render', 1000, 7930)
        assert p.map == '/Game/Maps/Main' and p.overlay_path == 'project-fixes' and p.share_root is None
        assert p.merged[0] == ('gc.TimeBetweenPurgingPendingKillObjects', '4')
        assert dict(p.cvar_overrides) == OVERRIDES

    def test_pilot_has_no_frame_and_no_peak(self):
        p = validate_job_data(job(kind='pilot', frame=5, end_frame=6))
        assert p.is_pilot and p.frame is None and p.measured_peak_mb is None

    def test_missing_kind_means_render(self):
        data = job()
        del data['unreal_kind']
        assert validate_job_data(data).kind == 'render'

    def test_no_overlay(self):
        p = validate_job_data(job(overlay=False))
        assert p.overlay_path == '' and p.overlay_sha256 == ''

    @pytest.mark.parametrize('change,field', [
        ({'id': 0}, 'id'), ({'id': True}, 'id'), ({'renderer': 'BLENDER'}, 'renderer'),
        ({'unreal_kind': 'audio'}, 'unreal_kind'), ({'unreal_measured_peak_mb': None}, 'unreal_measured_peak_mb'),
        ({'unreal_measured_peak_mb': True}, 'unreal_measured_peak_mb'),
        ({'unreal_measured_peak_mb': 1048577}, 'unreal_measured_peak_mb'),
        ({'end_frame': 1001}, 'end_frame'), ({'start_frame': '1000'}, 'start_frame'),
        ({'start_frame': 20000000, 'end_frame': 20000000}, 'start_frame'),
        ({'engine_build': '5.7.4-51494982'}, 'engine_build'), ({'engine_build': '5.8.04-1'}, 'engine_build'),
    ])
    def test_top_level_rejections(self, change, field):
        with pytest.raises(JobDataError) as info:
            validate_job_data(job(**change))
        assert info.value.field == field

    def test_pilot_with_a_peak_is_rejected(self):
        with pytest.raises(JobDataError, match='unreal_measured_peak_mb'):
            validate_job_data(job(kind='pilot', unreal_measured_peak_mb=5))

    @pytest.mark.parametrize('key,value', [
        ('source_type', 'blend_file'), ('uproject_path', '../x.uproject'), ('archive_path', 'incoming\\Demo.zip'),
        ('archive_path', 'incoming/Demo.rar'), ('archive_path', '\\\\evil\\share\\x.zip'),
        ('archive_sha256', 'xyz'), ('overlay_sha256', None), ('overlay_path', '../up'),
    ])
    def test_asset_rejections(self, key, value):
        data = job()
        data['asset'][key] = value
        with pytest.raises(JobDataError):
            validate_job_data(data)

    @pytest.mark.parametrize('settings', [
        {'unreal.map': '/Game/M', 'unreal.sequence': '/Game/S'},
        dict(job()['render_settings'], extra='x'),
        dict(job()['render_settings'], **{'unreal.output_format': 'EXR'}),
        dict(job()['render_settings'], **{'unreal.map': '-abslog=x'}),
    ])
    def test_render_settings_rejections(self, settings):
        with pytest.raises(JobDataError, match='render_settings'):
            validate_job_data(job(render_settings=settings))

    def test_output_format_defaults_to_png(self):
        settings = dict(job()['render_settings'])
        del settings['unreal.output_format']
        assert validate_job_data(job(render_settings=settings)).output_format == 'PNG'

    @pytest.mark.parametrize('overrides', [{'x.abslog': 'a'}, {'r.Foo': 'a b'}, {'nodot': '1'}, 'str'])
    def test_cvar_overrides_rejections(self, overrides):
        data = job()
        data['asset']['project_details']['unreal_cvar_overrides'] = overrides
        with pytest.raises(JobDataError, match='unreal_cvar_overrides'):
            validate_job_data(data)

    def test_null_overrides_mean_none(self):
        data = job()
        data['asset']['project_details']['unreal_cvar_overrides'] = None
        assert validate_job_data(data).cvar_overrides == ()


class TestMerge:

    def test_order_farm_first_then_sorted(self):
        merged = cvars.merge(overrides={'r.Z': '1', 'Constraints.NewEvaluation': '1', 'a.b': '2'})
        assert [n for n, _v in merged] == ['gc.TimeBetweenPurgingPendingKillObjects', 'a.b',
                                           'Constraints.NewEvaluation', 'r.Z']

    def test_precedence_farm_project_critical_case_insensitive(self):
        merged = cvars.merge(farm={'r.A': '1', 'r.B': '1'}, overrides={'R.a': '2'}, critical={'r.b': '3'})
        assert merged == [('r.A', '2'), ('r.B', '3')]

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            cvars.merge(overrides={'r.A': 'a;b'})

    def test_sha_is_of_the_dpcvars_string(self):
        merged = [('r.A', '1'), ('r.B', '2')]
        assert cvars.dpcvars_string(merged) == 'r.A=1,r.B=2'
        assert cvars.cvars_sha256(merged) != cvars.cvars_sha256([('r.A', '1'), ('r.B', '3')])


class TestEffective:
    MERGED = [('gc.TimeBetweenPurgingPendingKillObjects', '4'), ('Constraints.NewEvaluation', '1'),
              ('r.Streaming.PoolSize', '9000'), ('r.Other', 'abc')]

    def test_normalized_values_pass(self):
        effective = {'gc.TimeBetweenPurgingPendingKillObjects': '4.000000', 'Constraints.NewEvaluation': 'true',
                     'r.Streaming.PoolSize': '9000', 'r.Other': 'abc'}
        assert cvars.check_effective(effective, self.MERGED) == ('', [])

    def test_pool_or_farm_mismatch_fails(self):
        error, _ = cvars.check_effective({'r.streaming.poolsize': '7777'}, self.MERGED)
        assert error.startswith('CVar r.Streaming.PoolSize is 7777, not 9000; an engine-level override')

    def test_other_mismatch_warns(self):
        assert cvars.check_effective({'r.Other': 'xyz', 'Constraints.NewEvaluation': 'false'}, self.MERGED) == \
            ('', ['Constraints.NewEvaluation', 'r.Other'])

    def test_untrusted_names_and_values_are_ignored_or_sanitized(self):
        effective = {'not in merged.x': '1', 'r.Unknown': '1', 5: 'x', 'r.Other': 'a\x00bc' + 'x' * 100}
        error, warned = cvars.check_effective(effective, self.MERGED)
        assert error == '' and warned == ['r.Other']
        assert cvars.check_effective('nope', self.MERGED) == ('', [])
