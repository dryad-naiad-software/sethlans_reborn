# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU memory reading for Unreal and the sampler (Unreal foundation FR-WD-10,
FR-RT-16, D8; FR-T-5 "VRAM" reading part; the gate table belongs to the
executor). The ``nvidia-smi`` parsing moved to
``tests/unit/worker/gpu_memory/test_nvidia_smi.py``.
"""
import threading

from sethlans_worker_agent.unreal_executor import vram
from sethlans_worker_agent.unreal_executor.vram_sampler import VramSampler

LIVE = vram.GpuMemoryReading(99.0, 'nvidia-smi', (
    vram.GpuMem(0, 'NVIDIA GeForce RTX 5090', 32607, 1012, None, None, 'pci:0000:01:00.0'),), measures_card=True)


class TestReading:

    def test_live_reading_comes_from_the_reader_stamped_with_the_start_time(self):
        seen = []

        def read(pids):
            seen.append(pids())
            return LIVE

        reading = vram.take_reading(0, clock=lambda: 5.0, read=read, pids=lambda: frozenset({31392}),
                                    cards=lambda: frozenset({(0, 1, 0, 0)}))
        assert reading.taken_at == 5.0 and reading.gpus == LIVE.gpus and reading.measures_card is True
        assert seen == [frozenset({31392})]

    def test_override_never_calls_the_reader(self):
        def read(pids):
            raise AssertionError('reader called')

        reading = vram.take_reading(16384, clock=lambda: 1.0, read=read)
        assert reading.source == 'override'
        assert reading.gpus == (vram.GpuMem(0, '', 16384, 0, None, None),)
        assert reading.measures_card is False and reading.measures_process is False

    def test_no_reading_returns_none(self):
        assert vram.take_reading(0, read=lambda pids: None) is None

    def test_unexpected_exception_returns_none(self):
        def read(pids):
            raise RuntimeError('boom')

        assert vram.take_reading(0, read=read) is None


class TestPayload:

    def test_shape(self):
        reading = vram.GpuMemoryReading(10.0, 'nvidia-smi', (
            vram.GpuMem(0, 'NVIDIA GeForce RTX 5090', 32607, 1012, 0, 1012, 'pci:0000:01:00.0'),),
            measures_card=True, measures_process=True)
        assert vram.to_payload(reading, now=14.6) == {
            'schema': 1, 'source': 'nvidia-smi', 'age_s': 4, 'capabilities': {'card': True, 'process': True},
            'gpus': [{'index': 0, 'name': 'NVIDIA GeForce RTX 5090', 'total_mb': 32607,
                      'used_mb': 1012, 'sethlans_used_mb': 0, 'other_used_mb': 1012,
                      'card_key': 'pci:0000:01:00.0'}]}

    def test_none_and_bounds(self):
        assert vram.to_payload(None) is None
        gpus = tuple(vram.GpuMem(i, 'x' * 300, 1, 0, None, None) for i in range(70))
        payload = vram.to_payload(vram.GpuMemoryReading(100.0, 'override', gpus), now=0.0)
        assert payload['age_s'] == 0 and len(payload['gpus']) == 16
        assert len(payload['gpus'][0]['name']) == 128
        old = vram.to_payload(vram.GpuMemoryReading(0.0, 'override', gpus[:1]), now=10 ** 6)
        assert old['age_s'] == 86400


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class TestSampler:

    def test_latest_is_none_until_sampled(self):
        sampler = VramSampler(lambda: None, 30)
        assert sampler.latest() is None

    def test_fresh_returns_a_young_cached_reading_without_sampling(self):
        clock = Clock()
        calls = []
        sampler = VramSampler(lambda: calls.append(1) or vram.GpuMemoryReading(clock(), 'override', ()), 30, clock)
        sampler.sample_once()
        clock.now += 4
        assert sampler.fresh(5, deadline=clock.now + 1) is sampler.latest()
        assert len(calls) == 1

    def test_fresh_asks_the_thread_for_a_new_sample(self):
        clock = Clock()
        sampler = VramSampler(lambda: vram.GpuMemoryReading(clock(), 'override', ()), 3600, clock)
        sampler.sample_once()
        clock.now += 100
        sampler.start()
        try:
            got = sampler.fresh(5, deadline=clock.now + 1)
            for _ in range(500):
                if got is not None:
                    break
                threading.Event().wait(0.01)
                got = sampler.fresh(5, deadline=clock.now + 1)
            assert got is not None and got.taken_at == clock.now
        finally:
            sampler.stop(timeout=5)

    def test_fresh_gives_up_at_the_deadline(self):
        clock = Clock()
        sampler = VramSampler(lambda: None, 30, clock)
        assert sampler.fresh(5, deadline=clock.now - 1) is None

    def test_failed_sample_clears_the_reading(self):
        results = [vram.GpuMemoryReading(0.0, 'override', ()), RuntimeError('boom')]

        def sample():
            value = results.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        sampler = VramSampler(sample, 30)
        assert sampler.sample_once() is not None
        assert sampler.sample_once() is None and sampler.latest() is None

    def test_failing_source_is_sampled_at_most_twice_per_fresh_call(self):
        """D1: a sample that started after the request is final, even when it failed."""
        calls = []

        def failing():
            calls.append(1)
            return None

        sampler = VramSampler(failing, 3600)
        sampler.start()
        try:
            import time
            assert sampler.fresh(5, deadline=time.monotonic() + 2) is None
            assert len(calls) <= 2
        finally:
            sampler.stop(timeout=5)

    def test_slow_failure_is_final_without_respinning(self):
        clock = Clock()
        count = []

        def failing():
            count.append(1)
            clock.now += 1  # each sample takes "1 s"
            return None

        sampler = VramSampler(failing, 3600, clock)
        begun = clock.now
        sampler.start()
        try:
            assert sampler.fresh(5, deadline=clock.now + 1000, requested_at=begun) is None
            threading.Event().wait(0.05)  # room for a wrong extra sample to show
            assert len(count) == 1
        finally:
            sampler.stop(timeout=5)

    def test_never_succeeded(self):
        values = [None, vram.GpuMemoryReading(0.0, 'override', ()), None]
        sampler = VramSampler(lambda: values.pop(0), 30)
        assert sampler.never_succeeded() is False  # no sample yet
        sampler.sample_once()
        assert sampler.never_succeeded() is True
        sampler.sample_once()
        sampler.sample_once()
        assert sampler.never_succeeded() is False  # one success is enough

    def test_thread_is_a_named_daemon(self):
        sampler = VramSampler(lambda: None, 3600)
        sampler.start()
        try:
            assert sampler._thread.daemon and sampler._thread.name == 'unreal-vram-sampler'
        finally:
            sampler.stop(timeout=5)
