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
from sethlans_worker_agent.capacity.profile import SlotReservation
from sethlans_worker_agent.cards.in_use import CardInUse
from sethlans_worker_agent.gpu_device_script import (
    GPU_CHECK_EXIT_CODE, GPU_CHECK_MARKER,
    choose_backend, gpu_selection,
)
from sethlans_worker_agent.blender_failure import (
    describe_blender_failure, gpu_check_failure_message,
)

_RTX = CardInUse('pci:0000:01:00.0', 'NVIDIA GeForce RTX 4080', 'CUDA_NVIDIA GeForce RTX 4080_0000:01:00_OptiX', 'OPTIX')
_IGPU = CardInUse('pci:0000:0c:00.0', 'AMD Radeon(TM) Graphics', 'HIP_AMD Radeon(TM) Graphics_0000:0c:00', 'HIP')
_RTX_T = (_RTX.blender_id, 'OPTIX', _RTX.name)
_IGPU_T = (_IGPU.blender_id, 'HIP', _IGPU.name)


def _reservation(cards, own, mode='split', ignored=()):
    """A GPU reservation over the cards in use ``cards`` holding the keys of ``own``."""
    return SlotReservation(job_id=1, device_used='GPU', card_keys=tuple(c.key for c in own),
                           cards=tuple(cards), gpu_mode=mode, ignored=tuple(ignored))


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

    def test_returns_card_backend_and_full_list(self):
        assert gpu_selection(_reservation([_RTX, _IGPU], [_RTX])) == (
            'OPTIX', 'isolated', [_RTX_T, _IGPU_T], [(_RTX.blender_id, 'OPTIX')], [])

    def test_uses_the_cards_own_backend(self):
        backend, _mode, _cards, job_cards, _ignored = gpu_selection(_reservation([_RTX, _IGPU], [_IGPU]))

        assert backend == 'HIP'
        assert job_cards == [(_IGPU.blender_id, 'HIP')]

    def test_no_job_card_raises(self):
        with pytest.raises(ValueError, match='holds no graphics card'):
            gpu_selection(_reservation([_RTX], []))

    @pytest.mark.parametrize('bad_id', [None, '', 7])
    def test_job_card_without_id_raises(self, bad_id):
        bad = _RTX._replace(blender_id=bad_id)

        with pytest.raises(ValueError, match='no Blender device id'):
            gpu_selection(_reservation([bad, _IGPU], [bad]))

    @pytest.mark.parametrize('bad_type', ['VULKAN', 'CPU', None, 'optix'])
    def test_job_card_unknown_backend_raises(self, bad_type):
        bad = _RTX._replace(backend=bad_type)

        with pytest.raises(ValueError, match='unknown compute backend'):
            gpu_selection(_reservation([bad], [bad]))

    def test_bad_other_card_is_dropped_with_warning(self, caplog):
        other = CardInUse('pci:0000:02:00.0', 'x', 'x', 'VULKAN')

        with caplog.at_level(logging.WARNING, logger=gpu_device_script.__name__):
            result = gpu_selection(_reservation([_RTX, _IGPU._replace(blender_id=None), other], [_RTX]))

        assert result == ('OPTIX', 'isolated', [_RTX_T], [(_RTX.blender_id, 'OPTIX')], [])
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2
        assert 'Graphics card pci:0000:0c:00.0 has no Blender device id' in warnings[0]
        assert "Graphics card pci:0000:02:00.0 has unknown compute backend 'VULKAN'" in warnings[1]

    def test_missing_name_becomes_unknown(self):
        card = _RTX._replace(name='')

        assert gpu_selection(_reservation([card], [card]))[2] == [(_RTX.blender_id, 'OPTIX', 'unknown')]


class TestCombinedSelection:

    def test_selects_every_card_of_the_backend(self):
        rtx_b = _RTX._replace(key='pci:0000:02:00.0', blender_id='CUDA_NVIDIA GeForce RTX 4080_0000:02:00_OptiX')

        backend, mode, embedded, job_cards, _ignored = gpu_selection(
            _reservation([_RTX, rtx_b], [_RTX, rtx_b], 'combined'))

        assert (backend, mode) == ('OPTIX', 'combined')
        assert len(embedded) == 2
        assert job_cards == [(_RTX.blender_id, 'OPTIX'), (rtx_b.blender_id, 'OPTIX')]

    def test_mixed_families_raise(self):
        with pytest.raises(ValueError, match='not driven the same way'):
            gpu_selection(_reservation([_RTX, _IGPU], [_RTX, _IGPU], 'combined'))


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

    # HIP, not OPTIX: a missing OptiX entry adds a hint (test_blender_failure_optix_hint).
    DETAIL = "0 Blender device entries match HIP 'x'; wanted [HIP 'x']; Blender offers []"

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

    def test_marker_after_a_prefix_is_not_a_gpu_failure(self):
        """The helper writes the marker at column 0; anything else may be forged."""
        stderr = f"Error: {GPU_CHECK_MARKER} {self.DETAIL}\n"

        assert gpu_check_failure_message(87, stderr) is None

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

    @pytest.mark.parametrize('code', [1, -11, 88, 120])
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
