# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The first fill of the GPU details cache runs one detection (#253).

The agent loop and the web UI status thread can both ask for the GPU list
before it is cached. The list and the Blender that produced it must come
from one detection, because the periodic GPU check detects with that
Blender, and a filled cache is never replaced. Without an installed
Blender nothing is cached, so the next call detects.
"""
import json
import logging
import subprocess
import sys
import threading

import pytest

from sethlans_worker_agent import hardware_detection

RAW = [{'index': 0, 'name': 'AMD Radeon', 'type': 'HIP', 'id': 'HIP_A'}]
TIMEOUT = 10


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch):
    monkeypatch.setattr(hardware_detection, '_gpu_details_cache', None)
    monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', None)


@pytest.fixture
def find(mocker):
    return mocker.patch('sethlans_worker_agent.hardware_detection._find_any_blender_executable')


@pytest.fixture
def run(mocker):
    return mocker.patch('sethlans_worker_agent.hardware_detection.subprocess.run')


def _output(mocker, devices=RAW):
    return mocker.MagicMock(stdout=json.dumps(devices) + '\n')


class _WatchedLock:
    """The real lock, counting every thread that arrives to take it."""

    def __init__(self, lock):
        self._lock = lock
        self.arrivals = threading.Semaphore(0)

    def __enter__(self):
        self.arrivals.release()
        self._lock.acquire()
        return self

    def __exit__(self, *exc_info):
        self._lock.release()
        return False


class TestFirstFillFromTwoThreads:

    def test_one_detection_one_list_one_blender(self, mocker, monkeypatch, find, run):
        find.side_effect = ['blender-a', 'blender-b']
        inside, release = threading.Event(), threading.Event()
        commands = []

        def detection(command, **_kwargs):
            commands.append(command[0])
            inside.set()
            assert release.wait(TIMEOUT)
            devices = [dict(RAW[0], id=f'HIP_{len(commands)}')]  # each detection its own list
            return _output(mocker, devices)

        run.side_effect = detection
        lock = _WatchedLock(hardware_detection._gpu_details_lock)
        monkeypatch.setattr(hardware_detection, '_gpu_details_lock', lock)
        results = []

        def caller():
            results.append(hardware_detection.get_gpu_device_details())

        first, second = threading.Thread(target=caller), threading.Thread(target=caller)
        first.start()
        try:
            assert inside.wait(TIMEOUT)  # the first caller is inside its detection
            second.start()
            assert lock.arrivals.acquire(timeout=TIMEOUT)  # the first caller's arrival
            assert lock.arrivals.acquire(timeout=TIMEOUT)  # the second caller reached the lock
        finally:
            release.set()
            first.join(TIMEOUT)
            if second.ident is not None:
                second.join(TIMEOUT)

        assert not first.is_alive() and not second.is_alive()
        assert commands == ['blender-a']
        assert find.call_count == 1
        assert len(results) == 2
        assert results[0] is results[1]
        assert results[0] == [dict(RAW[0], id='HIP_1')]
        assert hardware_detection._gpu_details_blender_exe == 'blender-a'


class TestFilledCache:

    def test_filled_cache_is_returned_without_detecting(self, monkeypatch, find, run):
        cached = [dict(RAW[0])]
        monkeypatch.setattr(hardware_detection, '_gpu_details_cache', cached)
        monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', 'blender-a')
        find.return_value = 'blender-b'
        run.side_effect = subprocess.CalledProcessError(1, 'blender-b')  # would fail

        for _ in range(2):
            assert hardware_detection.get_gpu_device_details() is cached

        run.assert_not_called()
        find.assert_not_called()
        assert hardware_detection._gpu_details_blender_exe == 'blender-a'

    def test_later_call_does_not_replace_the_first_list(self, mocker, find, run):
        find.side_effect = ['blender-a', 'blender-b']
        run.side_effect = [_output(mocker), _output(mocker, [dict(RAW[0], id='HIP_OTHER')])]

        first = hardware_detection.get_gpu_device_details()
        second = hardware_detection.get_gpu_device_details()

        assert first == RAW and second is first
        assert run.call_count == 1
        assert hardware_detection._gpu_details_blender_exe == 'blender-a'

    def test_failed_first_detection_is_cached_and_not_retried(self, mocker, find, run):
        find.return_value = 'blender-a'
        run.side_effect = [subprocess.CalledProcessError(1, 'blender-a'), _output(mocker)]

        assert hardware_detection.get_gpu_device_details() == []
        assert hardware_detection.get_gpu_device_details() == []

        assert run.call_count == 1
        assert hardware_detection._gpu_details_blender_exe is None


class TestNoBlenderInstalled:

    def test_empty_list_is_not_cached_and_the_next_call_detects(self, mocker, find, run):
        find.return_value = None

        assert hardware_detection.get_gpu_device_details() == []
        assert hardware_detection.get_gpu_device_details() == []
        run.assert_not_called()
        assert hardware_detection._gpu_details_cache is None
        assert hardware_detection._gpu_details_blender_exe is None

        find.return_value = 'blender-a'
        run.return_value = _output(mocker)

        assert hardware_detection.get_gpu_device_details() == RAW
        assert run.call_count == 1
        assert hardware_detection._gpu_details_blender_exe == 'blender-a'


# Parseable JSON whose records _filter_preferred_gpus cannot use.
MALFORMED_OUTPUTS = [
    '[1]\n',
    '[{"index": 0, "name": "AMD Radeon", "id": "HIP_A"}]\n',
    '[{"name": "AMD Radeon", "type": "HIP", "id": "HIP_A"}]\n',
]


def _fill_twice_with_one_error(caplog, text):
    """Call twice; return the one ERROR record, which must name ``text`` and the restart."""
    with caplog.at_level(logging.DEBUG, logger=hardware_detection.__name__):
        assert hardware_detection.get_gpu_device_details() == []
        assert hardware_detection.get_gpu_device_details() == []
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert text in errors[0].getMessage()
    assert 'the worker will run without GPUs until it is restarted' in errors[0].getMessage()
    assert errors[0].exc_info is not None and errors[0].exc_info[0] is not None
    return errors[0]


class TestFailedFirstFill:
    """A failed first detection caches "no GPUs"; callers queued on the lock do not repeat it."""

    @pytest.mark.parametrize('error', [
        OSError(8, 'Exec format error'),
        FileNotFoundError(2, 'No such file or directory'),
        UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid start byte'),
        ValueError('embedded null byte'),
    ], ids=['os-error', 'file-not-found', 'undecodable-output', 'value-error'])
    def test_subprocess_error_is_cached_as_empty(self, mocker, find, run, caplog, error):
        find.return_value = 'blender-a'
        run.side_effect = [error, _output(mocker)]

        record = _fill_twice_with_one_error(caplog, 'Failed to execute or parse detect_gpus.py')

        assert record.exc_info[1] is error
        assert run.call_count == 1
        assert hardware_detection._gpu_details_cache == []
        assert hardware_detection._gpu_details_blender_exe is None

    def test_invalid_json_is_cached_as_empty(self, mocker, find, run, caplog):
        find.return_value = 'blender-a'
        run.side_effect = [mocker.MagicMock(stdout='[not json]\n'), _output(mocker)]

        record = _fill_twice_with_one_error(caplog, 'Failed to execute or parse detect_gpus.py')

        assert isinstance(record.exc_info[1], json.JSONDecodeError)
        assert run.call_count == 1
        assert hardware_detection._gpu_details_cache == []

    @pytest.mark.parametrize('stdout', MALFORMED_OUTPUTS, ids=['not-a-dict', 'no-type', 'no-index'])
    def test_malformed_device_record_is_cached_as_empty(self, mocker, find, run, caplog, stdout):
        find.return_value = 'blender-a'
        run.side_effect = [mocker.MagicMock(stdout=stdout), _output(mocker)]

        record = _fill_twice_with_one_error(caplog, 'unusable device list')

        assert isinstance(record.exc_info[1], (KeyError, TypeError, AttributeError))
        assert run.call_count == 1
        assert hardware_detection._gpu_details_cache == []
        assert hardware_detection._gpu_details_blender_exe is None

    def test_output_is_decoded_as_utf8_with_replacement(self, mocker, find, run):
        find.return_value = 'blender-a'
        run.return_value = _output(mocker)

        assert hardware_detection.get_gpu_device_details() == RAW

        kwargs = run.call_args.kwargs
        assert (kwargs['text'], kwargs['encoding'], kwargs['errors']) == (True, 'utf-8', 'replace')


class TestStoreOrder:

    def test_executable_is_stored_before_the_list(self, mocker, find, run):
        """A reader on the unlocked fast path that sees the list also sees its Blender.

        The two module globals are recorded before every line of the fill and
        at its return, so storing the list first would show up as a state
        with a list and no executable.
        """
        find.return_value = 'blender-a'
        run.return_value = _output(mocker)
        fill_code = hardware_detection._fill_gpu_details_cache.__code__
        module_globals = vars(hardware_detection)
        states = []

        def tracer(frame, event, _arg):
            if frame.f_code is not fill_code:
                return None
            if event in ('line', 'return'):
                states.append((module_globals['_gpu_details_blender_exe'], module_globals['_gpu_details_cache']))
            return tracer

        previous = sys.gettrace()
        sys.settrace(tracer)
        try:
            hardware_detection.get_gpu_device_details()
        finally:
            sys.settrace(previous)

        assert states[-1] == ('blender-a', RAW)
        assert [state for state in states if state[1] is not None and state[0] is None] == []
        assert ('blender-a', None) in states  # the executable was written first
