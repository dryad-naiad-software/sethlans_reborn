# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""GPU memory sampler: first sample on its own thread, wake rule, slow-sample warning (GPU memory reader FR-GU-6/12)."""
import logging
import threading
import time

import pytest

from sethlans_worker_agent.unreal_executor import vram_sampler
from sethlans_worker_agent.unreal_executor.vram import GpuMem, GpuMemoryReading
from sethlans_worker_agent.unreal_executor.vram_sampler import VramSampler

SAMPLER_LOG = 'sethlans_worker_agent.unreal_executor.vram_sampler'


def a_reading(taken_at=0.0):
    return GpuMemoryReading(taken_at, 'fake-a', (GpuMem(0, 'Test Card A', 8192, 512, None, None),), measures_card=True)


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class Gate:
    """A sample function that records its calls and, while ``held``, waits for ``release``."""

    def __init__(self, result=None, held=True):
        self.calls = []
        self.result = result
        self.entered = threading.Event()
        self.release = threading.Event()
        if not held:
            self.release.set()

    def __call__(self):
        self.calls.append(threading.current_thread().name)
        self.entered.set()
        assert self.release.wait(5), 'the test never released the sample'
        return self.result() if callable(self.result) else self.result


@pytest.fixture()
def slow_unlogged(monkeypatch):
    monkeypatch.setattr(vram_sampler, '_slow_logged', False)


def _until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'condition never held'
        threading.Event().wait(0.005)


class TestStartupSample:

    def test_start_takes_exactly_one_sample_and_fresh_with_requested_at_returns_it(self):
        gate = Gate(lambda: a_reading(time.monotonic()), held=False)
        sampler = VramSampler(gate, 3600)
        begun = time.monotonic()
        sampler.start()
        try:
            got = sampler.fresh(12, deadline=begun + 5, requested_at=begun)
            assert got is not None and got.taken_at >= begun
            assert gate.calls == ['unreal-vram-sampler']  # on the sampler thread, not this one
            assert not sampler._wake.is_set()  # nothing asked for a second sample
        finally:
            sampler.stop(timeout=5)
        assert gate.calls == ['unreal-vram-sampler']

    def test_a_failed_first_sample_running_at_the_request_is_final(self):
        gate = Gate(None)
        sampler = VramSampler(gate, 3600)
        begun = time.monotonic()
        sampler.start()
        try:
            assert gate.entered.wait(5)
            assert sampler.fresh(12, deadline=0, requested_at=begun) is None  # deadline passed: no wait
            assert not sampler._wake.is_set()
            gate.release.set()
            _until(lambda: sampler._samples == 1)
            assert gate.calls == ['unreal-vram-sampler'] and not sampler._wake.is_set()
        finally:
            gate.release.set()
            sampler.stop(timeout=5)

    def test_a_completed_failed_sample_after_the_request_is_final(self):
        clock = Clock(100.0)
        sampler = VramSampler(lambda: None, 3600, clock)
        sampler.sample_once()  # started at 100, failed, done before fresh() is called
        clock.now = 101.0
        assert sampler.fresh(12, deadline=10 ** 6, requested_at=100.0) is None
        assert not sampler._wake.is_set()  # it already answers the request: no extra sample

    def test_a_sample_started_after_requested_at_counts_even_when_fresh_is_called_later(self):
        clock = Clock(100.0)
        gate = Gate(a_reading(100.0))
        sampler = VramSampler(gate, 3600, clock)
        sampler.start()
        try:
            assert gate.entered.wait(5)
            clock.now = 105.0  # fresh() runs after the first sample began
            waiter = threading.Thread(target=lambda: results.append(
                sampler.fresh(12, deadline=10 ** 6, requested_at=100.0)), name='test-fresh')
            results = []
            waiter.start()
            gate.release.set()
            waiter.join(5)
            assert results == [gate.result] and len(gate.calls) == 1
        finally:
            gate.release.set()
            sampler.stop(timeout=5)


class TestWakeRule:

    def _running(self, clock):
        gate = Gate(a_reading())
        sampler = VramSampler(gate, 3600, clock)
        worker = threading.Thread(target=sampler.sample_once, name='test-sampling')
        worker.start()
        assert gate.entered.wait(5)
        return sampler, gate, worker

    def test_a_sample_running_since_the_request_is_not_asked_again(self):
        clock = Clock(100.0)
        sampler, gate, worker = self._running(clock)
        try:
            clock.now = 105.0
            assert sampler.fresh(5, deadline=0, requested_at=100.0) is None  # deadline passed: no wait
            assert not sampler._wake.is_set()
        finally:
            gate.release.set()
            worker.join(5)

    def test_a_sample_started_before_the_request_does_not_answer_it(self):
        clock = Clock(100.0)
        sampler, gate, worker = self._running(clock)
        try:
            clock.now = 105.0
            assert sampler.fresh(5, deadline=0) is None
            assert sampler._wake.is_set()  # one more sample is asked for
        finally:
            gate.release.set()
            worker.join(5)

    def test_no_sample_running_and_a_stale_reading_asks_for_one(self):
        clock = Clock(100.0)
        sampler = VramSampler(lambda: a_reading(clock()), 3600, clock)
        sampler.sample_once()
        clock.now += 60
        assert sampler.fresh(5, deadline=0) is None and sampler._wake.is_set()

    def test_a_young_reading_asks_for_nothing(self):
        clock = Clock(100.0)
        sampler = VramSampler(lambda: a_reading(clock()), 3600, clock)
        sampler.sample_once()
        clock.now += 4
        assert sampler.fresh(5, deadline=0) is sampler.latest() and not sampler._wake.is_set()

    def test_a_starting_sample_clears_the_request(self):
        sampler = VramSampler(lambda: None, 3600)
        sampler._wake.set()
        sampler.sample_once()
        assert not sampler._wake.is_set()


class TestSlowSample:

    def _sampler(self, durations):
        clock = Clock()

        def sample():
            clock.now += durations.pop(0)
            return None

        return VramSampler(sample, 3600, clock)

    def _warnings(self, caplog):
        return [r.getMessage() for r in caplog.records if r.name == SAMPLER_LOG and r.levelno == logging.WARNING]

    def test_a_sample_over_10_s_warns_once_per_process(self, slow_unlogged, caplog):
        sampler = self._sampler([11.0, 30.0])
        sampler.sample_once()
        sampler.sample_once()
        assert self._warnings(caplog) == ["GPU memory sample took 11 s"]

    def test_exactly_10_s_does_not_warn(self, slow_unlogged, caplog):
        sampler = self._sampler([10.0, 9.0])
        sampler.sample_once()
        sampler.sample_once()
        assert self._warnings(caplog) == []

    def test_the_warning_is_once_across_samplers(self, slow_unlogged, caplog):
        self._sampler([12.0]).sample_once()
        self._sampler([12.0]).sample_once()
        assert len(self._warnings(caplog)) == 1

    def test_a_failing_slow_sample_also_warns(self, slow_unlogged, caplog):
        clock = Clock()

        def sample():
            clock.now += 15.0
            raise RuntimeError('driver')

        VramSampler(sample, 3600, clock).sample_once()
        assert "GPU memory sample took 15 s" in self._warnings(caplog)


class TestNeverSucceeded:

    def test_true_once_the_thread_started_before_any_sample_ends(self):
        gate = Gate(a_reading())
        sampler = VramSampler(gate, 3600)
        assert sampler.never_succeeded() is False  # not started, no sample
        sampler.start()
        try:
            assert sampler.never_succeeded() is True
            assert gate.entered.wait(5) and sampler.never_succeeded() is True  # first sample still running
            gate.release.set()
            _until(lambda: sampler.latest() is not None)
            assert sampler.never_succeeded() is False
        finally:
            gate.release.set()
            sampler.stop(timeout=5)

    def test_stays_true_while_every_sample_fails(self):
        gate = Gate(None, held=False)
        sampler = VramSampler(gate, 3600)
        sampler.start()
        try:
            _until(lambda: gate.calls)
            assert sampler.never_succeeded() is True
        finally:
            sampler.stop(timeout=5)

    def test_start_twice_starts_one_thread(self):
        gate = Gate(None, held=False)
        sampler = VramSampler(gate, 3600)
        sampler.start()
        first = sampler._thread
        sampler.start()
        try:
            assert sampler._thread is first
        finally:
            sampler.stop(timeout=5)
