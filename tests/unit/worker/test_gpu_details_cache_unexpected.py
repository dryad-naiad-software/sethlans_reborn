# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
An unexpected error before the device list is filtered is not "no GPUs" (#253).

The first fill of the GPU details cache treats a failed or unreadable
detection, and a malformed device record, as "no GPUs" (cached). Any other
``KeyError``, ``TypeError`` or ``AttributeError`` raised while preparing or
starting the detection is a bug, not a detection result: it propagates, nothing
is cached, and the lock is released, so the next call detects again.
"""
import json

import pytest

from sethlans_worker_agent import hardware_detection

RAW = [{'index': 0, 'name': 'AMD Radeon', 'type': 'HIP', 'id': 'HIP_A'}]
ERRORS = [KeyError('PATH'), TypeError('expected str'), AttributeError('no attribute')]
ERROR_IDS = ['key-error', 'type-error', 'attribute-error']


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch):
    monkeypatch.setattr(hardware_detection, '_gpu_details_cache', None)
    monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', None)


@pytest.fixture
def run(mocker):
    mocker.patch('sethlans_worker_agent.hardware_detection._find_any_blender_executable',
                 return_value='blender-a')
    return mocker.patch('sethlans_worker_agent.hardware_detection.subprocess.run',
                        return_value=mocker.MagicMock(stdout=json.dumps(RAW) + '\n'))


def _assert_raises_uncached_and_unlocked(error):
    with pytest.raises(type(error)) as excinfo:
        hardware_detection.get_gpu_device_details()
    assert excinfo.value is error
    assert hardware_detection._gpu_details_cache is None
    assert hardware_detection._gpu_details_blender_exe is None
    assert hardware_detection._gpu_details_lock.locked() is False


class TestUnexpectedErrorBeforeTheFilter:

    @pytest.mark.parametrize('error', ERRORS, ids=ERROR_IDS)
    def test_error_from_the_environment_cleanup_propagates(self, mocker, run, error):
        mocker.patch('sethlans_worker_agent.env_cleanup.clean_env_for_blender', side_effect=[error, {}])

        _assert_raises_uncached_and_unlocked(error)
        run.assert_not_called()

        assert hardware_detection.get_gpu_device_details() == RAW  # the next call detects
        assert run.call_count == 1
        assert hardware_detection._gpu_details_blender_exe == 'blender-a'

    @pytest.mark.parametrize('error', ERRORS, ids=ERROR_IDS)
    def test_error_from_the_subprocess_call_propagates(self, mocker, run, error):
        run.side_effect = [error, run.return_value]

        _assert_raises_uncached_and_unlocked(error)

        assert hardware_detection.get_gpu_device_details() == RAW  # the next call detects
        assert run.call_count == 2
        assert hardware_detection._gpu_details_blender_exe == 'blender-a'
