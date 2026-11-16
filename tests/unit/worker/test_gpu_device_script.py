# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Worker-side unit tests for gpu_device_script (#238).

Covers the card selection the worker embeds in the render script and the
job error text built from a failed Blender run.
"""
import logging

import pytest

from sethlans_worker_agent import gpu_device_script
from sethlans_worker_agent.gpu_device_script import (
    GPU_CHECK_EXIT_CODE, GPU_CHECK_MARKER,
    choose_backend, combined_selection, describe_blender_failure,
    gpu_check_failure_message, isolated_selection,
)

_RTX = {'index': 0, 'name': 'NVIDIA GeForce RTX 4080', 'type': 'OPTIX',
        'id': 'CUDA_NVIDIA GeForce RTX 4080_0000:01:00_OptiX'}
_IGPU = {'index': 1, 'name': 'AMD Radeon(TM) Graphics', 'type': 'HIP',
         'id': 'HIP_AMD Radeon(TM) Graphics_0000:0c:00'}
_RTX_T = (_RTX['id'], 'OPTIX', _RTX['name'])
_IGPU_T = (_IGPU['id'], 'HIP', _IGPU['name'])


@pytest.fixture
def cards(mocker):
    """Patch the worker's card list; return a setter for other lists."""
    patched = mocker.patch(
        'sethlans_worker_agent.system_monitor.get_gpu_device_details',
        return_value=[_RTX, _IGPU],
    )

    def _set(value):
        patched.return_value = value

    return _set


class TestChooseBackend:

    @pytest.mark.parametrize('detected, expected', [
        (['CUDA', 'OPTIX'], 'OPTIX'),
        (['HIP', 'CUDA'], 'CUDA'),
        (['ONEAPI', 'HIP'], 'HIP'),
        (['ONEAPI', 'METAL'], 'METAL'),
        (['ONEAPI'], 'ONEAPI'),
        (['VULKAN'], None),
        ([], None),
        (None, None),
    ])
    def test_preference_order(self, detected, expected):
        assert choose_backend(detected) == expected


class TestIsolatedSelection:

    def test_returns_card_backend_and_full_list(self, cards):
        assert isolated_selection(0) == ('OPTIX', [_RTX_T, _IGPU_T], [(_RTX['id'], 'OPTIX')])

    def test_uses_the_cards_own_backend(self, cards):
        backend, _cards, job_cards = isolated_selection(1)

        assert backend == 'HIP'
        assert job_cards == [(_IGPU['id'], 'HIP')]

    @pytest.mark.parametrize('index', [2, 99, -1, '0', None, 0.0])
    def test_bad_index_raises(self, cards, index):
        with pytest.raises(ValueError, match='out of range'):
            isolated_selection(index)

    def test_no_cards_raises(self, cards):
        cards([])

        with pytest.raises(ValueError, match='detected 0 GPU'):
            isolated_selection(0)

    @pytest.mark.parametrize('bad_id', [None, '', 7])
    def test_job_card_without_id_raises(self, cards, bad_id):
        cards([dict(_RTX, id=bad_id), _IGPU])

        with pytest.raises(ValueError, match='no Blender device id'):
            isolated_selection(0)

    def test_job_card_missing_id_key_raises(self, cards):
        record = {k: v for k, v in _RTX.items() if k != 'id'}
        cards([record])

        with pytest.raises(ValueError, match='no Blender device id'):
            isolated_selection(0)

    @pytest.mark.parametrize('bad_type', ['VULKAN', 'CPU', None, 'optix'])
    def test_job_card_unknown_backend_raises(self, cards, bad_type):
        cards([dict(_RTX, type=bad_type)])

        with pytest.raises(ValueError, match='unknown compute backend'):
            isolated_selection(0)

    def test_job_card_not_a_dict_raises(self, cards):
        cards(['NVIDIA GeForce RTX 4080'])

        with pytest.raises(ValueError, match='is not a device record'):
            isolated_selection(0)

    def test_bad_other_card_is_dropped_with_warning(self, cards, caplog):
        cards([_RTX, dict(_IGPU, id=None), {'index': 2, 'type': 'VULKAN', 'id': 'x'}])

        with caplog.at_level(logging.WARNING, logger=gpu_device_script.__name__):
            result = isolated_selection(0)

        assert result == ('OPTIX', [_RTX_T], [(_RTX['id'], 'OPTIX')])
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2
        assert 'Detected GPU 1 has no Blender device id' in warnings[0]
        assert "Detected GPU 2 has unknown compute backend 'VULKAN'" in warnings[1]

    def test_missing_name_becomes_unknown(self, cards):
        cards([{'index': 0, 'type': 'OPTIX', 'id': _RTX['id']}])

        assert isolated_selection(0)[1] == [(_RTX['id'], 'OPTIX', 'unknown')]


class TestCombinedSelection:

    def test_selects_every_card_of_the_backend(self, cards):
        rtx_b = dict(_RTX, index=2, id='CUDA_NVIDIA GeForce RTX 4080_0000:02:00_OptiX')
        cards([_RTX, _IGPU, rtx_b])

        backend, embedded, job_cards = combined_selection('OPTIX')

        assert backend == 'OPTIX'
        assert len(embedded) == 3
        assert job_cards == [(_RTX['id'], 'OPTIX'), (rtx_b['id'], 'OPTIX')]

    @pytest.mark.parametrize('backend', ['VULKAN', 'CPU', None, ''])
    def test_unknown_backend_raises(self, cards, backend):
        with pytest.raises(ValueError, match='Unknown compute backend'):
            combined_selection(backend)

    def test_no_card_with_backend_raises(self, cards):
        with pytest.raises(ValueError, match='No detected GPU uses compute backend CUDA'):
            combined_selection('CUDA')

    def test_bad_records_are_dropped(self, cards, caplog):
        cards([_RTX, dict(_RTX, index=1, id='')])

        with caplog.at_level(logging.WARNING, logger=gpu_device_script.__name__):
            _backend, embedded, job_cards = combined_selection('OPTIX')

        assert embedded == [_RTX_T]
        assert job_cards == [(_RTX['id'], 'OPTIX')]
        assert 'Detected GPU 1 has no Blender device id' in caplog.text

    def test_only_bad_records_for_backend_raises(self, cards):
        cards([dict(_RTX, id=None), _IGPU])

        with pytest.raises(ValueError, match='No detected GPU uses compute backend OPTIX'):
            combined_selection('OPTIX')


class TestAppendSelectionGuards:

    @pytest.mark.parametrize('backend, mode', [
        ("OPTIX'); import os; #", 'isolated'),
        ('OPTIX', 'everything'),
    ])
    def test_invalid_backend_or_mode_raises(self, backend, mode):
        lines = []

        with pytest.raises(ValueError, match='Invalid GPU selection'):
            gpu_device_script._append_selection(lines, backend, mode, [], [])

        assert lines == []


class TestGpuCheckFailureMessage:

    DETAIL = "0 Blender device entries match OPTIX 'x'; wanted [OPTIX 'x']; Blender offers []"

    def test_marker_among_other_lines(self):
        stderr = (
            "Read blend: scene.blend\n"
            f"{GPU_CHECK_MARKER} {self.DETAIL}\n"
            "Blender quit\n"
        )

        message = gpu_check_failure_message(GPU_CHECK_EXIT_CODE, stderr)

        assert message.startswith('GPU device check failed:')
        assert message.endswith(self.DETAIL)
        assert GPU_CHECK_MARKER not in message

    def test_marker_after_a_prefix_is_found(self):
        stderr = f"Error: {GPU_CHECK_MARKER} {self.DETAIL}\n"

        assert self.DETAIL in gpu_check_failure_message(87, stderr)

    def test_describe_uses_the_gpu_message(self):
        stderr = f"noise\n{GPU_CHECK_MARKER} {self.DETAIL}\n"

        message = describe_blender_failure(87, stderr)

        assert message == gpu_check_failure_message(87, stderr)
        assert 'Blender exited with code' not in message

    def test_exit_87_without_marker_is_generic(self):
        assert gpu_check_failure_message(87, 'segfault\n') is None
        assert describe_blender_failure(87, 'segfault\n') == (
            'Blender exited with code 87. Details: segfault'
        )

    @pytest.mark.parametrize('code', [1, -11, 86, 88, 120])
    def test_other_failing_code_with_marker_is_gpu_check(self, code):
        """Interpreter shutdown can turn exit 87 into another code."""
        stderr = f"{GPU_CHECK_MARKER} {self.DETAIL}"

        message = describe_blender_failure(code, stderr)

        assert message == gpu_check_failure_message(code, stderr)
        assert message.startswith('GPU device check failed:')
        assert message.endswith(self.DETAIL)

    def test_exit_0_with_marker_is_not_a_failure(self):
        assert gpu_check_failure_message(0, f"{GPU_CHECK_MARKER} {self.DETAIL}") is None

    @pytest.mark.parametrize('stderr', [None, '', '   \n\t'])
    def test_empty_stderr(self, stderr):
        assert gpu_check_failure_message(87, stderr) is None
        assert describe_blender_failure(1, stderr) == (
            'Blender exited with code 1. Details: No STDERR.'
        )
        assert describe_blender_failure(87, stderr) == (
            'Blender exited with code 87. Details: No STDERR.'
        )

    def test_long_marker_detail_is_truncated(self):
        detail = 'd' * 5000

        message = gpu_check_failure_message(87, f"{GPU_CHECK_MARKER} {detail}")

        assert message.endswith(' ' + 'd' * 1000)
        assert 'd' * 1001 not in message

    def test_long_generic_stderr_is_truncated(self):
        message = describe_blender_failure(1, 'e' * 5000)

        assert message == 'Blender exited with code 1. Details: ' + 'e' * 500
