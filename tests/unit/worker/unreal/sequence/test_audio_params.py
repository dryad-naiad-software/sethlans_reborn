# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The audio job's payload, CVars, memory gate and command line (sequence rendering FR-SA-1, FR-SA-3, FR-SA-4,
FR-SR-5, D3, AC-15): kind ``audio`` with its run, lead-in and shot parts; the audio CVars in its farm tier only (and
out of the warm-up key); the pilot's need rule on the job's card without a live card reading; the four audio flags
on its render only.
"""
import pytest

from sethlans_worker_agent.unreal_executor import command, cvars, farm_defaults, job_params, sequence_prep
from sethlans_worker_agent.unreal_executor import vram_gate_check
from sethlans_worker_agent.unreal_executor.command import CommandError
from tests.unit.worker.unreal._executor_fakes import params, reading
from tests.unit.worker.unreal.sequence._pieces import audio_data, audio_params, piece_params
from tests.unit.worker.unreal.test_command import EDITOR, JOB, MAP, MERGED, QUEUE, UPROJECT

AUDIO_CVARS = {'au.NeverMuteNonRealtimeAudioDevices': '1', 'au.NeverDisableSubmixes': '1'}


class TestPayload:

    def test_an_audio_job_carries_its_run_lead_in_and_shots(self):
        p = audio_params()
        assert p.is_audio and not p.is_piece and not p.is_pilot and p.piece is None and p.frame is None
        run = p.audio
        assert (run.submission, run.start, run.end, run.lead, run.first, run.rendered) == (7, 100, 279, 8, 92, 188)
        assert run.shots == ((3, 100, 190), (4, 190, 280)) and p.measured_peak_mb is None

    @pytest.mark.parametrize('changes,field', [
        ({'sequence_submission': None}, 'sequence_submission'),
        ({'unreal_measured_peak_mb': 4000}, 'unreal_measured_peak_mb'),
        ({'unreal_lead_in_frames': 101}, 'unreal_lead_in_frames'),
        ({'end_frame': 99}, 'end_frame'),
        ({'shots': [[3, 100, 200]]}, 'render_settings.unreal.piece_shots'),
        ({'shots': [[3, 101, 280]]}, 'render_settings.unreal.piece_shots'),
    ])
    def test_bad_audio_payloads(self, changes, field):
        with pytest.raises(job_params.JobDataError) as caught:
            job_params.validate_job_data(audio_data(**changes))
        assert caught.value.field == field

    @pytest.mark.parametrize('key,value', [('unreal.resolution', [640, 360]), ('unreal.draft', {'motion_blur': False}),
                                           ('unreal.output_size', [64, 64]), ('unreal.shot_set_sha256', 'c' * 64)])
    def test_draft_resolution_and_piece_keys_never_apply(self, key, value):
        data = audio_data()
        data['render_settings'][key] = value
        with pytest.raises(job_params.JobDataError, match='set by sequence renders only'):
            job_params.validate_job_data(data)

    def test_exr_is_refused(self):
        data = audio_data()
        data['render_settings']['unreal.output_format'] = 'EXR'
        with pytest.raises(job_params.JobDataError) as caught:
            job_params.validate_job_data(data)
        assert caught.value.field == 'render_settings.unreal.output_format'


class TestCvars:

    def test_audio_cvars_are_in_the_audio_jobs_merged_list_only(self):
        merged = dict(audio_params().merged)
        assert {name: merged[name] for name in AUDIO_CVARS} == AUDIO_CVARS
        for other in (piece_params(), params(), params(kind='pilot')):
            assert not set(AUDIO_CVARS) & set(dict(other.merged))
        assert cvars.farm_tier(audio=False) is None

    def test_an_audio_job_shares_its_pieces_warm_up(self):
        audio, piece = audio_params(), piece_params()
        assert cvars.warmup_cvars_sha256(audio.merged) == cvars.warmup_cvars_sha256(piece.merged)
        assert cvars.cvars_sha256(audio.merged) != cvars.cvars_sha256(piece.merged)


class TestGate:

    def test_the_pilot_need_on_the_jobs_card_without_a_live_reading(self):
        gate = vram_gate_check.evaluate(audio_params(), reading(source='override'))
        pilot = vram_gate_check.evaluate(params(kind='pilot'), reading())
        assert gate.ok and gate.gate == 'audio' and gate.need_mb == pilot.need_mb
        assert 'vram_measured_peak_mb' not in gate.summary and gate.summary['vram_gate'] == 'audio'

    def test_a_card_too_small_fails_and_no_reading_fails(self):
        assert not vram_gate_check.evaluate(audio_params(), reading(used=25000)).ok
        assert not vram_gate_check.evaluate(audio_params(), None).ok


class TestCommand:

    def _ctx(self):
        return command.context_for(EDITOR, r'D:\uecache', 8668, graphics_adapter=1)

    def test_ac15_the_audio_render_has_the_four_flags_after_the_render_flags(self):
        args = list(command.build_render(self._ctx(), UPROJECT, MAP, QUEUE, JOB + r'\l.log', MERGED, audio=True).args)
        start = args.index(farm_defaults.RENDER_FLAGS[-1]) + 1
        assert args[start:start + 4] == farm_defaults.AUDIO_FLAGS and args[start + 4].startswith('-abslog=')

    def test_ac15_no_other_launch_carries_them(self):
        ctx = self._ctx()
        launches = [command.build_render(ctx, UPROJECT, MAP, QUEUE, JOB + r'\l.log', MERGED),
                    command.build_render(ctx, UPROJECT, MAP, QUEUE, JOB + r'\l.log', MERGED, pilot_launch=True)]
        for launch in launches:
            assert not set(farm_defaults.AUDIO_FLAGS) & set(launch.args)
            with pytest.raises(CommandError, match='only an audio job'):
                command.validate_args(list(launch.args[:5]) + ['-audiomixer'] + list(launch.args[5:]), ctx,
                                      pilot_launch=launch.pilot, phase='render')

    @pytest.mark.parametrize('change', ['drop', 'twice', 'value', 'after_zen'])
    def test_an_audio_render_needs_each_flag_once_as_given(self, change):
        ctx = self._ctx()
        args = list(command.build_render(ctx, UPROJECT, MAP, QUEUE, JOB + r'\l.log', MERGED, audio=True).args)
        command.validate_args(args, ctx, phase='render', audio=True)
        at = args.index('-AudioCallbackBufferFrameSize=32')
        if change == 'drop':
            del args[at]
        elif change == 'twice':
            args.insert(at, '-audiomixer')
        elif change == 'value':
            args[at] = '-AudioCallbackBufferFrameSize=1024'
        else:
            args.insert(len(args) - 2, args.pop(at))
        with pytest.raises(CommandError, match='an audio render must carry'):
            command.validate_args(args, ctx, phase='render', audio=True)


class TestPrepParams:

    def test_prepare_audio_renders_from_r0_and_names_the_first_kept_frame(self):
        data = sequence_prep.audio_params(audio_params(), r'D:\c\j\42-x\output', 'res', 'scripts')
        assert (data['mode'], data['range'], data['start']) == ('prepare_audio', [92, 280], 100)
        assert 'frame' not in data and 'resolution' not in data and 'draft' not in data

    def test_the_export_params(self):
        data = sequence_prep.export_params(audio_params(), r'D:\c\j\42-x\audio_src', r'D:\c\j\42-x\s.json', 's')
        assert (data['mode'], data['range'], data['max_sections'], data['max_export_bytes']) == \
            ('export_audio', [92, 280], 64, 4 * 1024 ** 3)
        assert set(data) == {'schema', 'job_id', 'mode', 'map', 'sequence', 'range', 'export_dir', 'result_path',
                             'scripts_dir', 'max_sections', 'max_export_bytes'}

    @pytest.mark.parametrize('value', [None, [30], [0, 1], [30, 0], [121, 1], [30000, 1002], ['30', 1], [True, 1]])
    def test_a_bad_frame_rate_from_the_prep_fails(self, value):
        with pytest.raises(sequence_prep.JobFailure, match=r'invalid result \(frame_rate\)'):
            sequence_prep.frame_rate({'frame_rate': value})

    def test_a_good_frame_rate(self):
        assert sequence_prep.frame_rate({'frame_rate': [30000, 1001]}) == (30000, 1001)
