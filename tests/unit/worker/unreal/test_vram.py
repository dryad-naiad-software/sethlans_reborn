# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU memory reading and sampler (Unreal foundation FR-WD-10, FR-RT-16, D8;
FR-T-5 "VRAM" reading part; the gate table belongs to the executor).
"""
import subprocess
import threading

import pytest

from sethlans_worker_agent.unreal_executor import vram
from sethlans_worker_agent.unreal_executor.vram_sampler import VramSampler

ONE_GPU = "0, NVIDIA GeForce RTX 5090, 32607, 1012\n"
TWO_GPUS = "0, NVIDIA GeForce RTX 4090, 24564, 2000\n1, NVIDIA GeForce RTX 3090, 24576, 100\n"
UUIDS = "0, GPU-aaa\n1, GPU-bbb\n"
QUERY_GPU_FLAG = vram.QUERY_GPU[1]
APPS_FLAG = vram.QUERY_APPS[1]
UUID_FLAG = vram.QUERY_UUIDS[1]


WDDM_APPS = ("GPU-aaa, 10512, [N/A]\n"
             "GPU-aaa, 31392, [N/A]\n")


def fake_run(outputs):
    calls = []

    def run(args):
        calls.append(args)
        return outputs.get(args[1])

    run.calls = calls
    return run


class TestParse:

    def test_one_gpu(self):
        assert vram.parse_query_gpu(ONE_GPU) == [(0, 'NVIDIA GeForce RTX 5090', 32607, 1012)]

    def test_two_gpus(self):
        assert [row[0] for row in vram.parse_query_gpu(TWO_GPUS)] == [0, 1]

    def test_na_fields(self):
        text = "0, [N/A], 8192, 10\n1, Tesla, [N/A], 10\n2, Tesla, 8192, [N/A]\n[N/A], X, 1, 1\n"
        assert vram.parse_query_gpu(text) == [(0, '', 8192, 10)]

    def test_name_with_comma_and_garbage_lines(self):
        text = "\n0, Quadro, Special, 4096, 5000\nnot a row\n"
        assert vram.parse_query_gpu(text) == [(0, 'Quadro, Special', 4096, 4096)]

    def test_compute_apps(self):
        assert vram.parse_compute_apps(WDDM_APPS) == [('GPU-aaa', 10512, None), ('GPU-aaa', 31392, None)]


@pytest.fixture()
def posix_attribution(monkeypatch):
    """Attribution through compute-apps is only attempted off Windows."""
    monkeypatch.setattr(vram, '_is_windows', lambda: False)


class TestReading:

    def test_nvidia_reading_without_attribution_under_wddm(self, posix_attribution):
        run = fake_run({QUERY_GPU_FLAG: ONE_GPU, APPS_FLAG: WDDM_APPS})
        reading = vram.take_reading(0, clock=lambda: 5.0, run=run, pids=lambda: frozenset({31392}))
        assert reading.source == 'nvidia-smi' and reading.taken_at == 5.0
        assert reading.gpus == (vram.GpuMem(0, 'NVIDIA GeForce RTX 5090', 32607, 1012, None, None),)
        assert [call[1] for call in run.calls] == [QUERY_GPU_FLAG, APPS_FLAG]  # no uuid query

    def test_windows_never_attributes(self, monkeypatch):
        monkeypatch.setattr(vram, '_is_windows', lambda: True)
        apps = "GPU-aaa, 100, 600\n"
        run = fake_run({QUERY_GPU_FLAG: ONE_GPU, APPS_FLAG: apps, UUID_FLAG: "0, GPU-aaa\n"})
        [gpu] = vram.take_reading(0, run=run, pids=lambda: frozenset({100})).gpus
        assert gpu.sethlans_used_mb is None and gpu.other_used_mb is None
        assert [call[1] for call in run.calls] == [QUERY_GPU_FLAG]  # compute-apps not even asked

    def test_attribution_when_numbers_are_available(self, posix_attribution):
        apps = "GPU-aaa, 100, 600\nGPU-aaa, 200, 300\nGPU-bbb, 300, 50\n"
        run = fake_run({QUERY_GPU_FLAG: TWO_GPUS, APPS_FLAG: apps, UUID_FLAG: UUIDS})
        reading = vram.take_reading(0, run=run, pids=lambda: frozenset({100, 300}))
        first, second = reading.gpus
        assert (first.sethlans_used_mb, first.other_used_mb) == (600, 1400)
        assert (second.sethlans_used_mb, second.other_used_mb) == (50, 50)

    def test_mixed_na_nulls_only_that_gpu(self, posix_attribution):
        apps = "GPU-aaa, 100, [N/A]\nGPU-bbb, 300, 40\n"
        run = fake_run({QUERY_GPU_FLAG: TWO_GPUS, APPS_FLAG: apps, UUID_FLAG: UUIDS})
        first, second = vram.take_reading(0, run=run, pids=lambda: frozenset({300})).gpus
        assert first.sethlans_used_mb is None and first.other_used_mb is None
        assert second.sethlans_used_mb == 40

    def test_unlisted_gpu_is_not_attributed(self, posix_attribution):
        apps = "GPU-bbb, 300, 40\n"
        run = fake_run({QUERY_GPU_FLAG: TWO_GPUS, APPS_FLAG: apps, UUID_FLAG: UUIDS})
        first, _second = vram.take_reading(0, run=run, pids=lambda: frozenset({300})).gpus
        assert first.sethlans_used_mb is None

    def test_override_never_runs_nvidia_smi(self):
        def run(args):
            raise AssertionError('nvidia-smi called')

        reading = vram.take_reading(16384, clock=lambda: 1.0, run=run)
        assert reading.source == 'override'
        assert reading.gpus == (vram.GpuMem(0, '', 16384, 0, None, None),)

    @pytest.mark.parametrize('outputs', [{}, {QUERY_GPU_FLAG: ''}, {QUERY_GPU_FLAG: 'garbage'}])
    def test_failure_returns_none(self, outputs):
        assert vram.take_reading(0, run=fake_run(outputs)) is None

    def test_unexpected_exception_returns_none(self):
        def run(args):
            raise RuntimeError('boom')

        assert vram.take_reading(0, run=run) is None

    @pytest.mark.parametrize('exc', [FileNotFoundError(), subprocess.TimeoutExpired('nvidia-smi', 10), OSError()])
    def test_run_nvidia_smi_swallows_errors(self, mocker, exc):
        mocker.patch('subprocess.run', side_effect=exc)
        assert vram.run_nvidia_smi(vram.QUERY_GPU) is None

    def test_run_nvidia_smi_uses_list_form_and_timeout(self, mocker):
        run = mocker.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, ONE_GPU, ''))
        assert vram.run_nvidia_smi(vram.QUERY_GPU) == ONE_GPU
        args, kwargs = run.call_args
        assert args[0] == [vram.nvidia_smi_executable()] + vram.QUERY_GPU[1:]
        assert kwargs['timeout'] == 10 and 'shell' not in kwargs

    def test_system32_binary_is_preferred_on_windows(self, monkeypatch, tmp_path):
        system32 = tmp_path / 'System32'
        system32.mkdir()
        monkeypatch.setattr(vram, '_is_windows', lambda: True)
        monkeypatch.setenv('SystemRoot', str(tmp_path))
        assert vram.nvidia_smi_executable() == 'nvidia-smi'  # not installed there: PATH
        (system32 / 'nvidia-smi.exe').write_bytes(b'MZ')
        assert vram.nvidia_smi_executable() == str(system32 / 'nvidia-smi.exe')

    def test_path_lookup_off_windows(self, monkeypatch):
        monkeypatch.setattr(vram, '_is_windows', lambda: False)
        assert vram.nvidia_smi_executable() == 'nvidia-smi'


class TestPayload:

    def test_shape(self):
        reading = vram.GpuMemoryReading(10.0, 'nvidia-smi', (
            vram.GpuMem(0, 'NVIDIA GeForce RTX 5090', 32607, 1012, 0, 1012),))
        assert vram.to_payload(reading, now=14.6) == {
            'schema': 1, 'source': 'nvidia-smi', 'age_s': 4,
            'gpus': [{'index': 0, 'name': 'NVIDIA GeForce RTX 5090', 'total_mb': 32607,
                      'used_mb': 1012, 'sethlans_used_mb': 0, 'other_used_mb': 1012}]}

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
        sampler.sample_once()
        sampler.start(initial_sample_done=True)
        try:
            assert sampler.fresh(5, deadline=clock.now + 1000) is None
            assert len(count) == 2
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
