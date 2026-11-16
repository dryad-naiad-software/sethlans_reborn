# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Which backend entry stands for each physical card (``_filter_preferred_gpus``).

Blender lists one entry per backend, so an RTX card appears as a CUDA entry
and an OPTIX entry (its id plus ``_OptiX``). The worker keeps one entry per
card: OptiX for an NVIDIA RTX card that offers it, else CUDA for an NVIDIA
card that offers it, else the first backend in the machine-wide order
(OPTIX, CUDA, HIP, METAL, ONEAPI). The cards come back in Blender device
index order.
"""
import pytest

from sethlans_worker_agent.hardware_detection import _filter_preferred_gpus

RTX = 'NVIDIA GeForce RTX 4080'
GTX = 'NVIDIA GeForce GTX 1080'


def _entry(index, backend, name, base_id):
    dev_id = f'{base_id}_OptiX' if backend == 'OPTIX' else base_id
    return {'index': index, 'name': name, 'type': backend, 'id': dev_id}


def _choice(entries):
    chosen = _filter_preferred_gpus(entries)
    assert len(chosen) == 1
    return chosen[0]['type']


class TestOneCard:

    @pytest.mark.parametrize('name, backends, expected', [
        (RTX, ['CUDA', 'OPTIX'], 'OPTIX'),
        (RTX, ['OPTIX', 'CUDA'], 'OPTIX'),
        (RTX, ['CUDA'], 'CUDA'),
        (GTX, ['CUDA', 'OPTIX'], 'CUDA'),
        (GTX, ['OPTIX'], 'OPTIX'),
        (RTX, ['HIP', 'ONEAPI'], 'HIP'),
        ('nvidia geforce rtx 3060', ['CUDA', 'OPTIX'], 'OPTIX'),
        ('AMD Radeon RX 7900 XTX', ['HIP'], 'HIP'),
        ('Intel Arc A770', ['ONEAPI'], 'ONEAPI'),
        ('Apple M2 Max', ['METAL'], 'METAL'),
        # Records current behaviour for names without the "NVIDIA" prefix, not a design decision.
        ('GeForce GTX 1080', ['CUDA', 'OPTIX'], 'OPTIX'),
    ], ids=[
        'rtx-optix-and-cuda', 'rtx-optix-listed-first', 'rtx-cuda-only', 'non-rtx-nvidia-both',
        'non-rtx-nvidia-optix-only', 'nvidia-neither-generic-order', 'name-case-ignored',
        'amd', 'intel', 'apple', 'no-nvidia-in-name-generic-order',
    ])
    def test_backend_chosen(self, name, backends, expected):
        entries = [_entry(i, backend, name, 'GPU_0000:01:00') for i, backend in enumerate(backends)]
        assert _choice(entries) == expected

    def test_the_chosen_entry_is_returned_unchanged(self):
        cuda = _entry(0, 'CUDA', RTX, 'CUDA_RTX_0000:01:00')
        optix = _entry(1, 'OPTIX', RTX, 'CUDA_RTX_0000:01:00')
        assert _filter_preferred_gpus([cuda, optix]) == [optix]

    def test_unknown_backend_only_is_dropped(self):
        assert _filter_preferred_gpus([_entry(0, 'VULKAN', 'Some GPU', 'VK_0')]) == []

    def test_no_devices(self):
        assert _filter_preferred_gpus([]) == []


class TestSeveralCards:

    def test_sorted_by_the_chosen_entry_index(self):
        entries = [
            _entry(0, 'CUDA', RTX, 'CUDA_RTX_0000:01:00'),
            _entry(1, 'CUDA', GTX, 'CUDA_GTX_0000:02:00'),
            _entry(2, 'HIP', 'AMD Radeon', 'HIP_AMD_0000:03:00'),
            _entry(3, 'OPTIX', RTX, 'CUDA_RTX_0000:01:00'),
            _entry(4, 'OPTIX', GTX, 'CUDA_GTX_0000:02:00'),
        ]

        chosen = _filter_preferred_gpus(entries)

        assert [(d['index'], d['type'], d['id']) for d in chosen] == [
            (1, 'CUDA', 'CUDA_GTX_0000:02:00'),
            (2, 'HIP', 'HIP_AMD_0000:03:00'),
            (3, 'OPTIX', 'CUDA_RTX_0000:01:00_OptiX'),
        ]

    def test_two_cards_of_the_same_model_stay_apart(self):
        entries = [
            _entry(0, 'CUDA', RTX, 'CUDA_RTX_0000:01:00'),
            _entry(1, 'CUDA', RTX, 'CUDA_RTX_0000:02:00'),
            _entry(2, 'OPTIX', RTX, 'CUDA_RTX_0000:01:00'),
            _entry(3, 'OPTIX', RTX, 'CUDA_RTX_0000:02:00'),
        ]

        assert [d['id'] for d in _filter_preferred_gpus(entries)] == [
            'CUDA_RTX_0000:01:00_OptiX', 'CUDA_RTX_0000:02:00_OptiX',
        ]
