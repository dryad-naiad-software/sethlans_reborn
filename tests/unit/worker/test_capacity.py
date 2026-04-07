# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the worker capacity module.

FR-24: Parametrized tests for compute_capacity_profile covering every
row of the capacity examples table in the spec.
FR-25: CPU thread cap formula tests covering every branch including
the 1-core defensive-assertion edge case.

Slot reservation, drift detection, and lock-ordering tests live in
test_capacity_slots.py, test_capacity_drift.py, and
test_capacity_lock_ordering.py respectively, to keep each file under
the 300-line Python limit.
"""
import pytest

from sethlans_worker_agent.capacity import (
    CapacityProfile,
    compute_capacity_profile,
    cpu_threads_for_blender,
    log_capacity_profile,
)


def _profile(**overrides):
    defaults = dict(
        detected_gpu_count=0,
        cpu_cores=16,
        force_cpu_only=False,
        force_gpu_only=False,
        force_gpu_index=None,
        gpu_mode='split',
        cpu_threads_config=0,
    )
    defaults.update(overrides)
    return compute_capacity_profile(**defaults)


# --- FR-24: Capacity examples table (binding test cases) ---

_CAPACITY_CASES = [
    # id, kwargs, expected (gpu_slot_count, cpu_slot_count, total)
    (
        'zero_gpu_16_cores',
        dict(detected_gpu_count=0, cpu_cores=16, gpu_mode='split'),
        (0, 1, 1),
    ),
    (
        'zero_gpu_16_cores_combined',
        dict(detected_gpu_count=0, cpu_cores=16, gpu_mode='combined'),
        (0, 1, 1),
    ),
    (
        'one_gpu_16_cores_split',
        dict(detected_gpu_count=1, cpu_cores=16, gpu_mode='split'),
        (1, 1, 2),
    ),
    (
        'two_gpu_16_cores_split',
        dict(detected_gpu_count=2, cpu_cores=16, gpu_mode='split'),
        (2, 1, 3),
    ),
    (
        'two_gpu_16_cores_combined',
        dict(detected_gpu_count=2, cpu_cores=16, gpu_mode='combined'),
        (1, 1, 2),
    ),
    (
        'four_gpu_16_cores_split',
        dict(detected_gpu_count=4, cpu_cores=16, gpu_mode='split'),
        (4, 1, 5),
    ),
    (
        'four_gpu_16_cores_combined',
        dict(detected_gpu_count=4, cpu_cores=16, gpu_mode='combined'),
        (1, 1, 2),
    ),
    (
        'one_gpu_one_core_split',
        dict(detected_gpu_count=1, cpu_cores=1, gpu_mode='split'),
        (1, 0, 1),
    ),
    (
        'one_gpu_16_cores_force_gpu_only',
        dict(
            detected_gpu_count=1, cpu_cores=16, gpu_mode='split',
            force_gpu_only=True,
        ),
        (1, 0, 1),
    ),
    (
        'zero_gpu_16_cores_force_cpu_only',
        dict(
            detected_gpu_count=0, cpu_cores=16, gpu_mode='split',
            force_cpu_only=True,
        ),
        (0, 1, 1),
    ),
    (
        'four_gpu_16_cores_force_gpu_index_2',
        dict(
            detected_gpu_count=4, cpu_cores=16, gpu_mode='split',
            force_gpu_index=2,
        ),
        (1, 1, 2),
    ),
    # Extra coverage: FORCE_CPU_ONLY trumps GPUs and GPU_MODE entirely.
    (
        'four_gpu_16_cores_force_cpu_only_trumps_gpus',
        dict(
            detected_gpu_count=4, cpu_cores=16, gpu_mode='combined',
            force_cpu_only=True,
        ),
        (0, 1, 1),
    ),
    # Extra coverage: FORCE_GPU_ONLY disables CPU slot even on many cores.
    (
        'two_gpu_32_cores_force_gpu_only',
        dict(
            detected_gpu_count=2, cpu_cores=32, gpu_mode='split',
            force_gpu_only=True,
        ),
        (2, 0, 2),
    ),
    # Extra coverage: 1-core box with 0 GPUs -> no capacity at all.
    (
        'zero_gpu_one_core_no_capacity',
        dict(detected_gpu_count=0, cpu_cores=1, gpu_mode='split'),
        (0, 0, 0),
    ),
]


@pytest.mark.parametrize(
    'kwargs,expected',
    [(case[1], case[2]) for case in _CAPACITY_CASES],
    ids=[case[0] for case in _CAPACITY_CASES],
)
def test_compute_capacity_profile_matches_spec_table(kwargs, expected):
    profile = _profile(**kwargs)
    assert isinstance(profile, CapacityProfile)
    gpu_expected, cpu_expected, total_expected = expected
    assert profile.gpu_slot_count == gpu_expected
    assert profile.cpu_slot_count == cpu_expected
    assert profile.total == total_expected


class TestCapacityProfileMetadata:

    def test_startup_gpu_count_reflects_input(self):
        profile = _profile(detected_gpu_count=3)
        assert profile.startup_gpu_count == 3

    def test_gpu_mode_propagates(self):
        profile = _profile(gpu_mode='combined', detected_gpu_count=2)
        assert profile.gpu_mode == 'combined'

    def test_force_gpu_index_propagates(self):
        profile = _profile(
            detected_gpu_count=4, force_gpu_index=2, gpu_mode='split',
        )
        assert profile.force_gpu_index == 2

    def test_profile_is_frozen(self):
        profile = _profile(detected_gpu_count=1)
        with pytest.raises(Exception):
            profile.gpu_slot_count = 99  # type: ignore[misc]


# --- FR-25: CPU thread cap formula ---

class TestCpuThreadsForBlender:

    def test_no_config_uses_ceiling(self):
        profile = _profile(cpu_cores=16, cpu_threads_config=0)
        assert profile.cpu_thread_ceiling == 15
        assert cpu_threads_for_blender(profile) == 15

    def test_config_below_ceiling_is_honored(self):
        profile = _profile(cpu_cores=16, cpu_threads_config=8)
        assert cpu_threads_for_blender(profile) == 8

    def test_config_at_ceiling_returns_ceiling(self):
        profile = _profile(cpu_cores=16, cpu_threads_config=15)
        assert cpu_threads_for_blender(profile) == 15

    def test_config_above_ceiling_is_silently_capped(self):
        profile = _profile(cpu_cores=16, cpu_threads_config=20)
        # Silently capped; no exception.
        assert cpu_threads_for_blender(profile) == 15

    def test_config_below_ceiling_logs_debug(self, caplog):
        caplog.set_level('DEBUG', logger='sethlans_worker_agent.capacity.profile')
        profile = _profile(cpu_cores=16, cpu_threads_config=4)
        cpu_threads_for_blender(profile)
        debug_msgs = [
            r.message for r in caplog.records
            if r.levelname == 'DEBUG'
        ]
        assert any('below ceiling' in m for m in debug_msgs)

    def test_one_core_raises_value_error(self):
        # FR-15 defensive guard. Manually build a profile because
        # compute_capacity_profile uses max(1, cores - 1) for the ceiling
        # and _profile_ would end up with effective == 1 on a 1-core box.
        # The defensive assertion exists to guard against future refactors
        # that bypass FR-2 and allow cpu_threads_effective < 1.
        broken_profile = CapacityProfile(
            gpu_slot_count=1,
            cpu_slot_count=0,
            cpu_thread_ceiling=0,
            cpu_threads_effective=0,
            startup_gpu_count=1,
            gpu_mode='split',
        )
        with pytest.raises(ValueError, match='FR-2'):
            cpu_threads_for_blender(broken_profile)

    def test_compute_capacity_profile_one_core_ceiling_is_one(self):
        # cores - 1 would be 0, but the implementation uses max(1, cores - 1)
        # to keep the ceiling well-defined. FR-2 still disables the CPU slot.
        profile = _profile(cpu_cores=1, detected_gpu_count=1)
        assert profile.cpu_thread_ceiling == 1
        assert profile.cpu_slot_count == 0


# --- log_capacity_profile (FR-5) ---

class TestLogCapacityProfile:

    def test_logs_at_info(self, caplog):
        caplog.set_level('INFO', logger='sethlans_worker_agent.capacity.profile')
        profile = _profile(detected_gpu_count=2, cpu_cores=16, gpu_mode='split')
        log_capacity_profile(
            profile, detected_gpu_count=2,
            force_cpu_only=False, force_gpu_only=False,
        )
        info_msgs = [
            r.message for r in caplog.records if r.levelname == 'INFO'
        ]
        assert any('Worker capacity profile' in m for m in info_msgs)
        assert any('gpu_slots=2' in m for m in info_msgs)
        assert any('cpu_slots=1' in m for m in info_msgs)
        assert any('total=3' in m for m in info_msgs)
