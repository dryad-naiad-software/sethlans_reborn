# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""GPU memory reading types (GPU memory reader FR-GR-2, FR-GR-4)."""
import dataclasses

import pytest

from sethlans_worker_agent.gpu_memory import types
from sethlans_worker_agent.gpu_memory.types import GpuMem, GpuMemoryReading, make_gpu, problem
from sethlans_worker_agent.unreal_executor import vram

KEY = 'pci:0000:0a:00.0'


def reading(*gpus):
    return GpuMemoryReading(1.0, 'fake-a', tuple(gpus), measures_card=True)


class TestOldConstructors:

    def test_gpu_mem_positional_six_fields_has_no_card_key(self):
        gpu = GpuMem(2, 'Test Card A', 8192, 512, None, None)
        assert gpu.card_key is None and (gpu.index, gpu.total_mb, gpu.used_mb) == (2, 8192, 512)

    def test_reading_positional_three_fields_measures_nothing(self):
        old = GpuMemoryReading(5.0, 'override', (GpuMem(0, '', 4096, 0, None, None),))
        assert old.measures_card is False and old.measures_process is False

    def test_new_fields_by_position_and_keyword(self):
        assert GpuMem(0, 'Test Card A', 8192, 512, 10, 502, KEY).card_key == KEY
        new = GpuMemoryReading(1.0, 'fake-a', (), True, True)
        assert (new.measures_card, new.measures_process) == (True, True)

    def test_types_are_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            GpuMem(0, 'Test Card A', 8192, 512, None, None).used_mb = 1

    def test_vram_re_exports_the_same_names_and_limits(self):
        assert vram.GpuMem is GpuMem and vram.GpuMemoryReading is GpuMemoryReading
        assert (vram.MAX_REPORTED_GPUS, vram.MAX_GPU_INDEX, vram.MAX_NAME_LENGTH, vram.MAX_AGE_SECONDS) == (
            16, 63, 128, 86400)
        assert types.MAX_REPORTED_GPUS == 16


class TestMakeGpu:

    def test_clamps_used_and_derives_other(self):
        assert make_gpu(0, 'Test Card A', 8192, 9000, 300, KEY) == GpuMem(0, 'Test Card A', 8192, 8192, 300, 7892, KEY)

    def test_sethlans_above_total_is_clamped_and_other_is_never_negative(self):
        assert make_gpu(0, 'Test Card A', 4096, 100, 5000) == GpuMem(0, 'Test Card A', 4096, 100, 4096, 0)

    def test_no_attribution_keeps_both_none(self):
        gpu = make_gpu(1, 'Test Card B', 4096, 100, None, KEY)
        assert (gpu.sethlans_used_mb, gpu.other_used_mb, gpu.card_key) == (None, None, KEY)

    def test_made_cards_pass_the_rules(self):
        assert problem(reading(make_gpu(0, 'Test Card A', 8192, 9000, 300, KEY))) == ''


class TestRules:
    """FR-GR-4: every rule a backend's reading must obey, one at a time."""

    def test_a_valid_reading_has_no_problem(self):
        good = reading(make_gpu(0, 'Test Card A', 8192, 512, 100, KEY),
                       make_gpu(1, 'Test Card B', 4096, 0, None, 'pci:0000:0b:00.0'),
                       make_gpu(2, 'Test Card C', 2048, 0, None))
        assert problem(good) == ''

    def test_sixteen_cards_pass_seventeen_fail(self):
        cards = [make_gpu(i, f'Card {i}', 1024, 0, None) for i in range(17)]
        assert problem(reading(*cards[:16])) == ''
        assert problem(reading(*cards)) == 'more than 16 cards'

    def test_duplicate_index(self):
        assert problem(reading(make_gpu(0, 'A', 1024, 0, None), make_gpu(0, 'B', 1024, 0, None))) == (
            'card indices are not unique integers')

    @pytest.mark.parametrize('index', ['0', 1.0, True])
    def test_index_must_be_an_int(self, index):
        assert problem(reading(GpuMem(index, 'A', 1024, 0, None, None))) == 'card indices are not unique integers'

    def test_duplicate_card_key_fails_but_several_unknown_keys_pass(self):
        assert problem(reading(make_gpu(0, 'A', 1024, 0, None, KEY), make_gpu(1, 'B', 1024, 0, None, KEY))) == (
            'card keys are not unique')
        assert problem(reading(make_gpu(0, 'A', 1024, 0, None), make_gpu(1, 'B', 1024, 0, None))) == ''

    @pytest.mark.parametrize('total,used', [(1024, -1), (1024, 1025), (1024, 1.5), (1024.0, 0), (1024, True)])
    def test_used_within_zero_and_total(self, total, used):
        assert problem(reading(GpuMem(3, 'A', total, used, None, None))) == 'card 3: used_mb is not within 0..total_mb'

    def test_used_equal_to_total_and_zero_pass(self):
        assert problem(reading(GpuMem(0, 'A', 1024, 1024, None, None), GpuMem(1, 'B', 1024, 0, None, None))) == ''

    @pytest.mark.parametrize('ours,other', [(None, 10), (10, None)])
    def test_attribution_both_or_neither(self, ours, other):
        assert problem(reading(GpuMem(0, 'A', 1024, 100, ours, other))) == 'card 0: attribution is half missing'

    @pytest.mark.parametrize('ours', [-1, 1025, '5'])
    def test_sethlans_within_zero_and_total(self, ours):
        assert problem(reading(GpuMem(0, 'A', 1024, 100, ours, 0))) == (
            'card 0: sethlans_used_mb is not within 0..total_mb')

    @pytest.mark.parametrize('other', [0, 61, -40])
    def test_other_is_used_minus_sethlans(self, other):
        assert problem(reading(GpuMem(0, 'A', 1024, 100, 40, other))) == 'card 0: other_used_mb does not match'

    def test_other_is_zero_when_sethlans_exceeds_used(self):
        assert problem(reading(GpuMem(0, 'A', 1024, 100, 300, 0))) == ''

    @pytest.mark.parametrize('name', ['Card\x00A', '  Card A', 'Card  A', 'x' * 129, None])
    def test_name_must_be_cleaned(self, name):
        assert problem(reading(GpuMem(0, name, 1024, 0, None, None))) == 'card 0: the name is not cleaned'

    def test_a_name_of_the_limit_passes(self):
        assert problem(reading(GpuMem(0, 'x' * 128, 1024, 0, None, None))) == ''

    def test_the_first_broken_card_is_named(self):
        bad = reading(make_gpu(0, 'A', 1024, 0, None), GpuMem(1, 'B', 1024, 2000, None, None))
        assert problem(bad) == 'card 1: used_mb is not within 0..total_mb'
