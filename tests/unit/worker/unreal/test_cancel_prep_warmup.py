# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""FR-UE-3 cancel watcher, FR-UE-7/8/25 prep params and result checks, FR-UE-9/10 warm-up marker."""
import json
import threading
import time

import pytest

from sethlans_worker_agent.unreal_executor import cvars, farm_defaults, queue_prep, warmup
from sethlans_worker_agent.unreal_executor.cancel_watch import CancelCause, CancelWatcher, classify
from tests.unit.worker.unreal._executor_fakes import params


class TestCancel:

    def test_first_writer_wins(self):
        cause = CancelCause()
        assert not cause.is_set() and cause.cause is None
        assert cause.set('reassigned') and not cause.set('canceled')
        assert cause.is_set() and cause.cause == 'reassigned'
        with pytest.raises(ValueError):
            cause.set('bogus')

    @pytest.mark.parametrize('result,worker,expected', [
        (None, 1, None), (('RENDERING', 1), 1, None), (('CANCELED', 1), 1, 'canceled'),
        (('QUEUED', None), 1, 'reassigned'), (('RENDERING', 2), 1, 'reassigned'), (('DONE', 1), 1, 'reassigned'),
    ])
    def test_classify(self, result, worker, expected):
        assert classify(result, worker) == expected

    def _watch(self, get, shutdown=None):
        cause = CancelCause()
        watcher = CancelWatcher(9, cause, get_assignment=get, worker_id=1, shutdown_event=shutdown, poll_seconds=0.01)
        watcher.start()
        return cause, watcher

    def test_thread_detects_cancel_and_ignores_errors(self):
        calls = []

        def get(job_id):
            calls.append(job_id)
            if len(calls) == 1:
                raise RuntimeError('network down')
            return None if len(calls) < 3 else ('CANCELED', 1)
        cause, watcher = self._watch(get)
        deadline = time.monotonic() + 5
        while not cause.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        watcher.stop()
        assert cause.cause == 'canceled' and watcher._thread.name == 'unreal-cancel-9' and watcher._thread.daemon

    def test_shutdown_event(self):
        shutdown = threading.Event()
        shutdown.set()
        cause, watcher = self._watch(lambda job_id: ('RENDERING', 1), shutdown)
        deadline = time.monotonic() + 5
        while not cause.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        watcher.stop()
        assert cause.cause == 'shutdown'

    def test_a_thread_that_failed_to_start_is_not_joined(self, monkeypatch):
        """Review L3: stop() after a failed start() neither raises nor joins an unstarted thread."""
        def refuse(self):
            raise RuntimeError("can't start new thread")
        monkeypatch.setattr(threading.Thread, 'start', refuse)
        watcher = CancelWatcher(9, CancelCause(), get_assignment=lambda job_id: None, worker_id=1,
                                shutdown_event=None, poll_seconds=0.01)
        with pytest.raises(RuntimeError):
            watcher.start()
        watcher.stop()
        assert watcher._thread is None


class TestPrepParams:

    def test_render_params(self):
        data = queue_prep.prep_params(params(), 'D:\\c\\j\\42-x\\output', 'D:\\c\\j\\42-x\\prep_result.json')
        assert data['mode'] == 'prepare' and data['frame'] == 1000 and data['schema'] == 1
        assert data['package_root'] == '/Game/_SethlansFarm/Job42'
        assert data['file_name_format'] == 'sethlans_42.{frame_number}'
        assert data['cvar_names'][0] == 'gc.TimeBetweenPurgingPendingKillObjects'

    def test_pilot_params_have_no_frame(self):
        data = queue_prep.prep_params(params(kind='pilot'), 'o', 'r')
        assert data['mode'] == 'prepare_pilot' and 'frame' not in data and data['max_pilot_frames'] == 256

    def test_write_json_is_atomic_and_leaves_no_temp(self, tmp_path):
        queue_prep.write_json(tmp_path / 'p.json', {'a': 1})
        assert json.loads((tmp_path / 'p.json').read_text()) == {'a': 1}
        assert [p.name for p in tmp_path.iterdir()] == ['p.json']


def _result(**changes):
    data = {'ok': True, 'queue_asset': '/Game/_SethlansFarm/Job42/Q_Job42', 'preset_kind': 'graph',
            'playback_range': [0, 1928], 'camera_cut_starts': [0, 99], 'effective_cvars': {}, 'warnings': []}
    data.update(changes)
    return data


class TestPrepResult:
    ROOT = '/Game/_SethlansFarm/Job42'

    def parse(self, data, pilot=False):
        return queue_prep.parse_result(data, self.ROOT, pilot, '/Game/Seq/Edit')

    def test_valid(self):
        result = self.parse(_result(warnings=['a\x07b' + 'x' * 3000], scalability=3))
        assert result.queue_asset.endswith('Q_Job42') and result.scalability == 3
        assert result.warnings[0].startswith('abx') and len(result.warnings[0]) == 2048

    @pytest.mark.parametrize('change,field', [
        ({'queue_asset': '/Game/Other/Q'}, 'queue_asset'), ({'queue_asset': '/Game/_SethlansFarm/Job420/Q'},
                                                            'queue_asset'),
        ({'queue_asset': 5}, 'queue_asset'), ({'preset_kind': 'video'}, 'preset_kind'),
        ({'playback_range': [0]}, 'playback_range'), ({'playback_range': [0, '9']}, 'playback_range'),
        ({'camera_cut_starts': ['x']}, 'camera_cut_starts'), ({'effective_cvars': []}, 'effective_cvars'),
        ({'scalability': 9}, 'scalability'), ({'warnings': 'x'}, 'warnings'),
    ])
    def test_invalid_fields(self, change, field):
        with pytest.raises(queue_prep.PrepError, match=rf'invalid result \({field}\)'):
            self.parse(_result(**change))

    def test_error_result_is_sanitized(self):
        with pytest.raises(queue_prep.PrepError) as info:
            self.parse({'ok': False, 'error': 'bad\r\nthing' + 'y' * 4000})
        assert str(info.value).startswith('badthing') and len(str(info.value)) == 2048

    def test_missing_result(self):
        with pytest.raises(queue_prep.PrepError, match='no readable result'):
            self.parse(None)

    def test_pilot_frames(self):
        result = self.parse(_result(pilot_frames=[0, 99, 202], queue_jobs=3), pilot=True)
        assert result.pilot_frames == (0, 99, 202) and result.queue_jobs == 3

    @pytest.mark.parametrize('frames,jobs,message', [
        ([-5, 0], 2, 'shots before frame 0'), ([], 0, 'No camera cut found in /Game/Seq/Edit'),
        (list(range(257)), 257, 'Sequence has 257 shots; the pilot supports at most 256.'),
        ([0, 99], 3, 'pilot_frames'), ([99, 0], 2, 'pilot_frames'), ([0, 2000], 2, 'pilot_frames'),
        ([0, 0], 2, 'pilot_frames'),
    ])
    def test_pilot_frame_rules(self, frames, jobs, message):
        with pytest.raises(queue_prep.PrepError, match=message):
            self.parse(_result(pilot_frames=frames, queue_jobs=jobs), pilot=True)

    def test_result_ok_and_load_limits(self, tmp_path):
        path = tmp_path / 'r.json'
        assert not queue_prep.result_ok(path)
        path.write_text('{"ok": true}')
        assert queue_prep.result_ok(path)
        path.write_text('not json')
        assert queue_prep.load(path) is None

    def test_warmup_params_use_pilot_frames(self):
        prep = self.parse(_result(pilot_frames=[0, 99], queue_jobs=2), pilot=True)
        data = queue_prep.warmup_params(params(kind='pilot'), prep, 'r.json', 24, 4, 240)
        assert data['frames'] == [0, 99] and data['mode'] == 'warmup' and data['scalability'] == 4
        assert data['camera_cut_starts'] == [0, 99] and data['hold_seconds'] == 240


class TestWarmupMarker:

    def test_path(self, tmp_path):
        assert warmup.marker_path(tmp_path, 'c' * 64, '5.8.4-1').relative_to(tmp_path).parts == \
            ('warmup', 'c' * 16, '5.8.4-1.json')
        assert warmup.marker_path(tmp_path, '', 'b').parent.name == '0' * 16

    def test_needs_record_and_remove(self, tmp_path):
        path = warmup.marker_path(tmp_path, '', 'b')
        assert warmup.needs_warmup(path, '/m', '/s', 'sha')
        warmup.record_success(path, '/m', '/s', 'sha')
        warmup.record_success(path, '/m', '/s2', 'sha')
        assert not warmup.needs_warmup(path, '/m', '/s', 'sha')
        data = json.loads(path.read_text())
        assert data['farm_defaults_version'] == farm_defaults.FARM_DEFAULTS_VERSION and len(data['warmed']) == 2
        assert warmup.needs_warmup(path, '/m', '/s', 'other-sha')
        warmup.remove_pair(path, '/m', '/s')
        assert warmup.needs_warmup(path, '/m', '/s', 'sha') and not warmup.needs_warmup(path, '/m', '/s2', 'sha')

    def test_a_cvar_change_replaces_the_list(self, tmp_path):
        path = warmup.marker_path(tmp_path, '', 'b')
        warmup.record_success(path, '/m', '/s', 'one')
        warmup.record_success(path, '/m', '/t', 'two')
        assert json.loads(path.read_text())['warmed'] == [{'map': '/m', 'sequence': '/t'}]

    def test_old_farm_defaults_and_garbage_need_warmup(self, tmp_path):
        path = warmup.marker_path(tmp_path, '', 'b')
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'warmed': [{'map': '/m', 'sequence': '/s'}], 'farm_defaults_version': 1,
                                    'cvars_sha256': 'sha'}))
        assert warmup.needs_warmup(path, '/m', '/s', 'sha')
        path.write_text('garbage')
        assert warmup.needs_warmup(path, '/m', '/s', 'sha')

    def test_decide(self, tmp_path):
        path = warmup.marker_path(tmp_path, '', 'b')
        normal = params()
        sha = cvars.cvars_sha256(normal.merged)
        assert warmup.decide(path, normal, sha)
        warmup.record_success(path, normal.map, normal.sequence, sha)
        assert warmup.decide(path, normal, sha) is None
        assert warmup.decide(path, params(kind='pilot'), sha) == 'pilot (always warms up)'
