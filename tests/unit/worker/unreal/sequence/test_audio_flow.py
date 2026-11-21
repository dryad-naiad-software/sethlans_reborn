# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``AudioFlow``, an audio job's UE phases, against a fake phase runner that writes what UE would: the prep result,
the render's ``.wav`` and log, and the export step's sources and manifest (sequence rendering FR-SA-2..10, D14,
D23, D24, AC-16, AC-46): the measured offset, the ``.wav`` checks, the fault lines, and an export that fails or
returns something invalid, which never fails the job.
"""
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

import pytest

from sethlans_worker_agent.unreal_executor import audio_flow, wav_check
from sethlans_worker_agent.unreal_executor.adapter_choice import Choice
from sethlans_worker_agent.unreal_executor.job_flow import FlowConfig
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
from sethlans_worker_agent.unreal_executor.summary import Summary
from tests.unit.worker.unreal._executor_fakes import outcome, reading
from tests.unit.worker.unreal.sequence._pieces import audio_params
from tests.unit.worker.unreal.sequence._signals import RATE, SPF, program, wav_bytes
from tests.unit.worker.unreal.sequence.test_range_flow import CARD, KEY, RENDER_LOG

LAG = 9418
R0 = 92                         # audio_params(): run 100-279 after an 8-frame audio lead-in
FRAMES = 188
BED = program((R0 + FRAMES) * SPF + 2 * RATE, seed=4)
WAV_NAME = 'sethlans_42.MasterSubmixDefault.wav'


@dataclass(frozen=True)
class Ctx:
    graphics_adapter: Optional[int] = None


class Runner:
    """Prep (attempt 1), warm-up, render and the export step (prep attempt 2), each writing UE's files."""

    def __init__(self, job, samples=FRAMES * SPF, log_extra=(), manifest=None, export_error=None, sources=None):
        self.job, self.launches, self.runs = job, [], []
        self.samples, self.log_extra, self.manifest = samples, list(log_extra), manifest
        self.export_error, self.sources = export_error, sources

    def launch(self, phase, attempt, launch, **kwargs):
        self.launches.append((phase, attempt, launch, kwargs))
        log = self.job.log(phase, attempt)
        if phase == 'prep' and attempt == 1:
            self.job.prep_result.write_text(json.dumps({
                'ok': True, 'queue_asset': '/Game/_SethlansFarm/Job42/Q_Job42', 'preset_kind': 'legacy',
                'playback_range': [0, 1000], 'frame_rate': [30, 1], 'camera_cut_starts': [0, 95, 190, 500],
                'effective_cvars': {}, 'queue_jobs': 1, 'warnings': []}), encoding='utf-8')
        elif phase == 'render':
            render = [BED[R0 * SPF + p + LAG] for p in range(self.samples)]
            (self.job.output / WAV_NAME).write_bytes(wav_bytes(render))
            log.write_text('\n'.join(RENDER_LOG + self.log_extra) + '\n', encoding='utf-8')
        elif phase == 'prep':
            self._export()
        return SimpleNamespace(phase=phase, outcome=outcome(wall_s=4.0), log_path=log)

    def _export(self):
        if self.export_error is not None:
            raise self.export_error
        params = json.loads((self.job.output.parent / 'export_params.json').read_text(encoding='utf-8'))
        folder = self.job.output.parent / 'audio_src'
        for name, data in (self.sources or {'src_000.wav': wav_bytes(BED)}).items():
            (folder / name).write_bytes(data)
        manifest = self.manifest or {'ok': True, 'sections': [
            {'start': 0, 'end': 400, 'start_offset_samples': 0, 'volume': 1.0, 'looping': False, 'wav': 'src_000.wav',
             'sample_rate': RATE}, {'unsupported': 'a sound cue'}]}
        with open(params['result_path'], 'w', encoding='utf-8') as handle:
            json.dump(manifest, handle)


def _flow(tmp_path, **runner_kw):
    for name in ('output', 'logs', 'scripts'):
        (tmp_path / name).mkdir(exist_ok=True)
    job = SimpleNamespace(params=tmp_path / 'params.json', prep_result=tmp_path / 'prep_result.json',
                          warmup_params=tmp_path / 'warmup_params.json', warmup_result=tmp_path / 'warmup_result.json',
                          scripts=tmp_path / 'scripts', output=tmp_path / 'output', logs=tmp_path / 'logs',
                          script=lambda name: tmp_path / 'scripts' / name,
                          log=lambda phase, attempt: tmp_path / 'logs' / f'{phase}.{attempt}.log')
    runner = Runner(job, **runner_kw)
    builders = SimpleNamespace(build_prep=lambda *a: ('prep', a), build_warmup=lambda *a: ('warmup', a),
                               build_render=lambda *a, **k: ('render', a, k))
    config = FlowConfig(step_frames=24, step_seconds=1, hold_seconds=1, quit_grace_s=5.0, log_max_bytes=1 << 20,
                        profile=str(tmp_path / 'profile'))
    flow = audio_flow.AudioFlow(audio_params(), Ctx(), job, runner, Summary(), builders, tmp_path / 'marker.json',
                                'Demo.uproject', config, reading=reading(gpus=(CARD,)),
                                choose=lambda job_id, key=None: Choice(job_id, KEY, CARD.name, 1), first_key=KEY)
    return flow, runner, job


def _json(path):
    return json.loads(path.read_text(encoding='utf-8'))


class TestRun:

    def test_prep_render_checks_and_the_measured_offset(self, tmp_path):
        flow, runner, job = _flow(tmp_path)
        result = flow.run()
        assert [(phase, attempt) for phase, attempt, *_ in runner.launches] == [
            ('prep', 1), ('warmup', 1), ('render', 1), ('prep', 2)]
        prep = _json(job.params)
        assert (prep['mode'], prep['range'], prep['start']) == ('prepare_audio', [92, 280], 100)
        assert _json(job.warmup_params)['frames'] == [92, 95, 190]  # FR-SW-6: r0 and the cuts it renders through
        render_launch = runner.launches[2][2]
        assert render_launch[0] == 'render' and render_launch[2] == {'pilot_launch': False, 'audio': True}
        export_launch = runner.launches[3][2]
        assert export_launch[1][2].name == 'sethlans_audio_export.py' and export_launch[1][3].name == 'prep.2.log'
        sound = result.sound
        assert sound.path.name == WAV_NAME and result.outputs == [sound.path] and sound.frames == FRAMES
        assert (sound.info.rate, sound.info.channels, sound.info.samples) == (RATE, 2, FRAMES * SPF)
        offset = sound.offset
        assert offset['status'] == 'measured' and abs(offset['offset_samples'] - LAG) <= 32, offset
        assert [row['shot'] for row in offset['shots']] == [3, 4] and offset['unsupported_sections'] == 1
        assert offset['export_s'] == 4.0 and not (tmp_path / 'audio_src').exists()
        s = flow.summary
        assert (s.get('audio_run'), s.get('audio_lead_in'), s.get('audio_samples'), s.get('audio_rate'),
                s.get('audio_channels'), s.get('sound_offset')) == ('100-279', '8', str(FRAMES * SPF), '48000', '2',
                                                                    'measured')
        assert s.get('latency_compensation_skipped') == '0' and not sound.latency_skipped

    def test_the_skipped_latency_compensation_is_reported(self, tmp_path):
        line = ('[2026.11.20-10.00.00:000][  0]LogMovieRenderPipeline: Warning: Audio latency compensation (7 frames '
                '= 22400 samples) exceeds shot 0 segment size (8000 samples). Skipping compensation.')
        flow, _runner, _job = _flow(tmp_path, log_extra=[line], samples=(FRAMES + 7) * SPF)
        sound = flow.run().sound
        assert sound.latency_skipped is True and flow.summary.get('latency_compensation_skipped') == '1'
        assert sound.info.expected == (FRAMES + 7) * SPF  # MRG kept the head and still drained 7 tail frames
        flow, _runner, _job = _flow(tmp_path, log_extra=[line])
        with pytest.raises(JobFailure, match=f"the run needs {(FRAMES + 7) * SPF}"):
            flow.run()

    @pytest.mark.parametrize('text', ['skipping compensation then Audio latency compensation', 'Audio latency comp'])
    def test_the_warning_needs_both_phrases_in_order(self, tmp_path, text):
        flow, _runner, _job = _flow(tmp_path, log_extra=['[2026.11.20-10.00.00:000][  0]' + text])
        assert flow.run().sound.latency_skipped is False


class TestFailures:

    @pytest.mark.parametrize('word', ['LogAudio: Error: seektable is invalid', 'Decoder error on stream',
                                      'Sound was preventing playback', 'Assertion failed: NumberOfLoadsInFlight == 0'])
    def test_ac16_a_streaming_fault_line_fails_the_job(self, tmp_path, word):
        flow, runner, _job = _flow(tmp_path, log_extra=['[2026.11.20-10.00.00:000][  0]' + word])
        with pytest.raises(JobFailure) as caught:
            flow.run()
        assert caught.value.message == wav_check.FAULT_MESSAGE
        assert ('prep', 2) not in [launch[:2] for launch in runner.launches]  # no export after a failed render

    def test_ac16_a_wav_300_samples_short_fails_the_job(self, tmp_path):
        flow, _runner, _job = _flow(tmp_path, samples=FRAMES * SPF - 300)
        with pytest.raises(JobFailure, match=f"has {FRAMES * SPF - 300} samples; the run needs {FRAMES * SPF}"):
            flow.run()

    def test_256_samples_long_is_accepted(self, tmp_path):
        flow, _runner, _job = _flow(tmp_path, samples=FRAMES * SPF + 256)
        assert flow.run().sound.info.samples == FRAMES * SPF + 256


class TestExportNeverFailsTheJob:

    def test_a_failed_export_launch_is_unavailable(self, tmp_path):
        flow, _runner, _job = _flow(tmp_path, export_error=JobFailure("Unreal prep pass timed out after 1200 s."))
        offset = flow.run().sound.offset
        assert offset['status'] == 'unavailable' and offset['offset_samples'] is None
        assert offset['reason'] == "the export step failed (Unreal prep pass timed out after 1200 s.)"
        assert flow.summary.get('sound_offset') == 'unavailable' and not (tmp_path / 'audio_src').exists()

    def test_an_export_error_is_named(self, tmp_path):
        flow, _runner, _job = _flow(tmp_path, manifest={'ok': False, 'error': 'A sound wave could not be exported.'})
        assert flow.run().sound.offset['reason'] == "the export step failed: A sound wave could not be exported."

    @pytest.mark.parametrize('rows,field', [
        ([{'start': 0, 'end': 400, 'start_offset_samples': 0, 'volume': 1.0, 'looping': False,
           'wav': '..\\outside.wav', 'sample_rate': RATE}], 'sections[0].wav'),
        ([{'unsupported': 'a sound cue'}] * 65, 'sections'),
        ([{'start': 0, 'end': 400, 'start_offset_samples': 0, 'volume': 1.0, 'looping': False,
           'wav': 'src_001.wav', 'sample_rate': RATE}], 'sections[0].wav'),
        ([{'start': 0, 'end': 400, 'start_offset_samples': 0, 'volume': 1.0, 'looping': False,
           'wav': 'src_000.wav', 'sample_rate': 44_100}], 'sections[0].sample_rate'),
    ], ids=['outside', '65-sections', 'missing-file', 'rate-mismatch'])
    def test_ac46_an_invalid_manifest_is_unavailable_naming_the_field(self, tmp_path, rows, field):
        flow, _runner, _job = _flow(tmp_path, manifest={'ok': True, 'sections': rows})
        offset = flow.run().sound.offset
        assert offset['status'] == 'unavailable'
        assert offset['reason'] == f"the sound export returned an invalid result ({field})"

    def test_an_unreadable_source_is_unavailable(self, tmp_path):
        flow, _runner, _job = _flow(tmp_path, sources={'src_000.wav': b'RIFF' + b'\x00' * 40})
        assert flow.run().sound.offset['reason'] == "the sound export returned an invalid result (sections[0].wav)"


class TestCollect:

    def test_one_wav_and_no_image(self, tmp_path):
        out = tmp_path / 'out'
        out.mkdir()
        with pytest.raises(JobFailure, match='wrote 0 .wav files'):
            wav_check.collect(out)
        (out / 'a.wav').write_bytes(b'x')
        (out / 'notes.txt').write_bytes(b'x')
        assert wav_check.collect(out).name == 'a.wav'
        (out / 'b.WAV').write_bytes(b'x')
        with pytest.raises(JobFailure, match='wrote 2 .wav files'):
            wav_check.collect(out)
        (out / 'b.WAV').unlink()
        (out / 'sethlans_42.0100.png').write_bytes(b'x')
        with pytest.raises(JobFailure, match='1 image or video files'):
            wav_check.collect(out)

    def test_a_wav_in_a_subdirectory_fails(self, tmp_path):
        (tmp_path / 'out' / 'shot1').mkdir(parents=True)
        (tmp_path / 'out' / 'shot1' / 'a.wav').write_bytes(b'x')
        with pytest.raises(JobFailure, match='subdirectory'):
            wav_check.collect(tmp_path / 'out')
