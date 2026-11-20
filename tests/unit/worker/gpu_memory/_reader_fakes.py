# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fakes for the GPU memory reader tests: a clock, scripted backends and made-up cards (no vendor assumed)."""
import threading

from sethlans_worker_agent.gpu_memory import reader as reader_module
from sethlans_worker_agent.gpu_memory.types import GpuMemoryReading, make_gpu

KEY_A = 'pci:0000:0a:00.0'
KEY_B = 'pci:0000:0b:00.0'
PIDS = frozenset({4242, 4343})


def card(index=0, name='Test Card A', total=8192, used=512, ours=None, key=KEY_A):
    return make_gpu(index, name, total, used, ours, key)


def reading(*gpus, source='fake-a', taken_at=0.0):
    return GpuMemoryReading(taken_at, source, tuple(gpus) if gpus else (card(),), measures_card=True)


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeBackend:
    """A scripted backend. ``samples`` entries are readings, ``None`` or exceptions; the last one repeats.

    ``duration`` advances ``clock`` inside every sample; ``block`` (an Event) makes the next samples wait for it.
    """

    def __init__(self, name, log, opens=True, samples=None, close_error=None, clock=None, duration=0.0):
        self.name = name
        self.log = log
        self.opens = opens
        self.samples = list(samples) if samples is not None else [reading(source=name)]
        self.close_error = close_error
        self.clock = clock
        self.duration = duration
        self.block = None
        self.entered = threading.Event()

    def open(self):
        self.log.append((self.name, 'open'))
        if isinstance(self.opens, Exception):
            raise self.opens
        return self.opens

    def sample(self, pids):
        self.log.append((self.name, 'sample', pids))
        self.entered.set()
        if self.block is not None:
            assert self.block.wait(5), 'the test never released the sample'
        if self.clock is not None:
            self.clock.now += self.duration
        value = self.samples.pop(0) if len(self.samples) > 1 else self.samples[0]
        self.log.append((self.name, 'sample-end'))
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.log.append((self.name, 'close'))
        if self.close_error is not None:
            raise self.close_error


def make_reader(*backends, setting='auto', platform='linux', clock=None):
    """A reader over ``backends`` (by name); returns ``(reader, created)``, ``created`` listing factory calls."""
    created = []

    def factory(backend):
        def make():
            created.append(backend.name)
            return backend
        return make

    factories = {backend.name: factory(backend) for backend in backends}
    reader = reader_module.GpuMemoryReader(setting=setting, platform=platform, factories=factories,
                                           clock=clock or Clock())
    return reader, created


def only(monkeypatch, *names):
    """Make ``auto`` try exactly ``names`` in order (the real table has one candidate per platform today)."""
    monkeypatch.setattr(reader_module, 'candidates', lambda setting, platform=None: tuple(names))
