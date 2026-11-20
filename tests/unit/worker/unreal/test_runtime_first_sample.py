# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unreal startup and the first GPU memory sample (GPU memory reader FR-GU-5, FR-GU-8 (NF-6), FR-GU-12, AC-27).

The real ``runtime._start_threads`` runs with a fake reader. The 12 s deadline is never waited for:
the sampler's clock is shifted past it, so ``fresh()`` gives up at once.
"""
import threading
import time

import pytest

from sethlans_worker_agent import config_unreal, unreal_share
from sethlans_worker_agent.gpu_memory import nvidia_smi
from sethlans_worker_agent.gpu_memory import reader as gpu_reader
from sethlans_worker_agent.unreal_executor import capabilities, runtime, vram
from sethlans_worker_agent.unreal_executor.vram import GpuMem, GpuMemoryReading
from sethlans_worker_agent.unreal_executor.vram_sampler import VramSampler
from tests._unreal_worker_state import enable_unreal, unreal_runtime_reset  # noqa: F401

pytestmark = pytest.mark.usefixtures('unreal_runtime_reset')

REAL_START_THREADS = runtime._start_threads


class FakeReader:
    """Records the thread of every ``read``; blocks while ``release`` is not set."""

    def __init__(self, held=True):
        self.threads = []
        self.entered = threading.Event()
        self.release = threading.Event()
        if not held:
            self.release.set()

    def read(self, pids):
        self.threads.append(threading.current_thread().name)
        self.entered.set()
        assert self.release.wait(5), 'the test never released the sample'
        return GpuMemoryReading(time.monotonic(), 'fake-a', (
            GpuMem(0, 'Test Card A', 8192, 512, None, None, 'pci:0000:0a:00.0'),), measures_card=True)


class PastDeadlineSampler(VramSampler):
    """The sampler with its clock already past the first-sample deadline (no real 12 s wait)."""

    def __init__(self, sample_fn, interval_seconds):
        shift = runtime.FIRST_SAMPLE_DEADLINE_S + 1
        super().__init__(sample_fn, interval_seconds, clock=lambda: time.monotonic() + shift)


@pytest.fixture()
def real_threads(monkeypatch):
    """Unreal enabled with fakes, but the real ``_start_threads`` (share thread off, override off)."""
    enable_unreal(monkeypatch)
    monkeypatch.setattr(runtime, '_start_threads', REAL_START_THREADS)
    monkeypatch.setattr(unreal_share, 'start', lambda: None)
    monkeypatch.setattr(config_unreal, 'VRAM_MB', 0)
    monkeypatch.setattr(config_unreal, 'VRAM_SAMPLE_SECONDS', 3600)
    monkeypatch.setattr(vram, 'unreal_card_set', lambda: frozenset({(0, 0x0a, 0, 0)}))  # FakeReader's card (FR-GU-2)


@pytest.fixture()
def fake_reader(monkeypatch):
    reader = FakeReader()
    monkeypatch.setattr(gpu_reader, 'get_reader', lambda: reader)
    yield reader
    reader.release.set()  # the sampler thread must be free before the runtime reset joins it


def _until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'condition never held'
        threading.Event().wait(0.005)


def test_no_vram_reason_text():
    assert runtime.NO_VRAM_REASON == ("no GPU memory reading on this machine (see the worker log); "
                                      "SETHLANS_UNREAL_VRAM_MB overrides it")
    assert len(runtime.NO_VRAM_REASON) <= 200 and runtime.FIRST_SAMPLE_DEADLINE_S == 12.0


class TestBlockingFirstSample:

    def test_startup_gives_up_at_the_deadline_and_reports_no_reading(self, real_threads, fake_reader, monkeypatch):
        monkeypatch.setattr(runtime, 'VramSampler', PastDeadlineSampler)
        begun = time.monotonic()
        runtime.startup()
        assert time.monotonic() - begun < 5  # not blocked by the hung sample
        assert fake_reader.entered.wait(5)  # the first sample is still stuck in the reader
        sampler = runtime.sampler()
        assert sampler is not None and sampler.latest() is None and sampler.never_succeeded() is True
        snap = capabilities.snapshot()
        assert snap['enabled'] is False and snap['disabled_reason'] == runtime.NO_VRAM_REASON
        assert capabilities.gpu_memory_payload() is None

    def test_no_reader_call_runs_on_the_calling_thread(self, real_threads, fake_reader, monkeypatch):
        monkeypatch.setattr(runtime, 'VramSampler', PastDeadlineSampler)
        runtime.startup()
        assert fake_reader.entered.wait(5)
        fake_reader.release.set()
        _until(lambda: runtime.sampler().latest() is not None)
        assert fake_reader.threads == ['unreal-vram-sampler']
        assert threading.current_thread().name not in fake_reader.threads

    def test_a_late_sample_then_enables_unreal(self, real_threads, fake_reader, monkeypatch):
        monkeypatch.setattr(runtime, 'VramSampler', PastDeadlineSampler)
        runtime.startup()
        assert capabilities.snapshot()['disabled_reason'] == runtime.NO_VRAM_REASON
        fake_reader.release.set()
        _until(lambda: not runtime.sampler().never_succeeded())
        snap = capabilities.snapshot()
        assert snap['enabled'] is True and snap['disabled_reason'] == ''
        payload = capabilities.gpu_memory_payload()
        assert payload['source'] == 'fake-a' and payload['gpus'][0]['card_key'] == 'pci:0000:0a:00.0'

    def test_startup_waits_for_the_first_sample_with_the_12_s_deadline(self, real_threads, fake_reader, monkeypatch):
        calls = []
        real_fresh = VramSampler.fresh

        def spy(self, max_age_s, deadline, requested_at=None):
            calls.append((max_age_s, deadline, requested_at))
            return real_fresh(self, max_age_s, deadline, requested_at)

        monkeypatch.setattr(VramSampler, 'fresh', spy)
        fake_reader.release.set()
        before = time.monotonic()
        runtime.startup()
        [(max_age, deadline, requested_at)] = calls
        assert max_age == 12.0 and deadline == requested_at + 12.0 and before <= requested_at <= time.monotonic()
        assert fake_reader.threads == ['unreal-vram-sampler']
        assert capabilities.snapshot()['enabled'] is True  # an answer within the deadline: enabled at once


class TestPublishAndReset:

    def test_the_sampler_is_published_before_the_wait_while_status_still_says_not_run(
            self, real_threads, fake_reader, monkeypatch):
        seen = []
        real_fresh = VramSampler.fresh

        def spy(self, max_age_s, deadline, requested_at=None):
            seen.append((runtime.sampler() is self, runtime.status(())))
            return real_fresh(self, max_age_s, deadline, requested_at)

        monkeypatch.setattr(VramSampler, 'fresh', spy)
        fake_reader.release.set()
        runtime.startup()
        assert seen == [(True, (False, 'Unreal startup has not run'))]

    def test_reset_fails_loudly_when_the_sampler_thread_does_not_stop(self):
        class Stuck:
            def stop(self, timeout=None):
                return False

        runtime._sampler = Stuck()
        with pytest.raises(RuntimeError, match='did not stop'):
            runtime.reset_for_tests()
        assert runtime.sampler() is None

    def test_sampler_stop_reports_whether_the_thread_ended(self):
        sampler = VramSampler(lambda: None, 3600)
        assert sampler.stop(timeout=1) is True  # never started
        started = VramSampler(lambda: None, 3600)
        started.start()
        assert started.stop(timeout=5) is True and not started._thread.is_alive()


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, name):
        def record(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f'{name} called')
        return record


@pytest.fixture()
def no_reader(monkeypatch):
    """Records (and fails) every way into the reader or ``nvidia-smi``."""
    recorder = Recorder()
    monkeypatch.setattr(gpu_reader, 'get_reader', recorder('get_reader'))
    monkeypatch.setattr(gpu_reader.GpuMemoryReader, 'read', recorder('read'))
    monkeypatch.setattr(gpu_reader.GpuMemoryReader, '__init__', recorder('GpuMemoryReader'))
    monkeypatch.setattr(nvidia_smi, 'run_nvidia_smi', recorder('run_nvidia_smi'))
    return recorder


class TestNf6:

    def test_unreal_off_never_creates_or_calls_the_reader(self, monkeypatch, no_reader):
        monkeypatch.setattr(config_unreal, 'ENABLED', False)
        capabilities.startup()
        capabilities.snapshot()
        capabilities.poll_params(node_idle=True)
        assert capabilities.gpu_memory_payload() is None
        assert no_reader.calls == [] and gpu_reader._reader is None
        assert runtime.sampler() is None

    def test_vram_override_never_creates_or_calls_the_reader(self, real_threads, monkeypatch, no_reader):
        monkeypatch.setattr(config_unreal, 'VRAM_MB', 16384)
        runtime.startup()
        sampler = runtime.sampler()
        assert sampler.latest() is not None and sampler.latest().source == 'override'
        sampler.fresh(0, deadline=time.monotonic() + 5)  # one more sample, through the same path
        assert no_reader.calls == [] and gpu_reader._reader is None
        assert capabilities.snapshot()['enabled'] is True
        assert capabilities.gpu_memory_payload()['capabilities'] == {'card': False, 'process': False}
