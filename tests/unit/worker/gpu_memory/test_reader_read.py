# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""GPU memory reader: reads, checks, statistics, close and the singleton (GPU memory reader FR-GR-3..6)."""
import logging
import threading

import pytest

from sethlans_worker_agent.gpu_memory import reader as reader_module
from sethlans_worker_agent.gpu_memory.types import GpuMem, GpuMemoryReading
from tests.unit.worker.gpu_memory._reader_fakes import PIDS, Clock, FakeBackend, card, make_reader, reading

READER_LOG = 'sethlans_worker_agent.gpu_memory.reader'


def chosen(*later, clock=None, duration=0.0, log=None):
    """A reader whose backend is chosen by a first read; ``later`` are the samples after it."""
    backend = FakeBackend('nvidia-smi', log if log is not None else [], clock=clock, duration=duration,
                          samples=[reading(source='nvidia-smi')] + list(later or [reading(source='nvidia-smi')]))
    reader, _created = make_reader(backend, clock=clock)
    assert reader.read(lambda: PIDS) is not None
    return reader, backend


class TestNeverRaises:

    def test_a_sample_exception_gives_none(self):
        reader, _backend = chosen(RuntimeError('driver'))
        assert reader.read(lambda: PIDS) is None

    def test_a_pids_exception_gives_none(self):
        reader, _backend = chosen()

        def pids():
            raise ProcessLookupError('gone')

        assert reader.read(pids) is None

    def test_first_failure_at_error_with_traceback_then_debug(self, caplog):
        caplog.set_level(logging.DEBUG, logger=READER_LOG)
        reader, _backend = chosen(RuntimeError('driver'))
        reader.read(lambda: PIDS)
        reader.read(lambda: PIDS)
        failures = [record for record in caplog.records if record.getMessage() == "GPU memory reading failed."]
        assert [record.levelno for record in failures] == [logging.ERROR, logging.DEBUG]
        assert all(record.exc_info is not None for record in failures)

    def test_a_selection_that_raises_gives_none(self, monkeypatch):
        reader, _created = make_reader(FakeBackend('nvidia-smi', []))

        def broken(setting, platform=None):
            raise ValueError('bad table')

        monkeypatch.setattr(reader_module, 'candidates', broken)
        assert reader.read(lambda: PIDS) is None


class TestChecks:

    def test_a_reading_with_no_card_is_none(self):
        reader, _backend = chosen(GpuMemoryReading(1.0, 'nvidia-smi', ()))
        assert reader.read(lambda: PIDS) is None

    def test_a_reading_that_breaks_a_rule_is_dropped_with_one_warning(self, caplog):
        bad = GpuMemoryReading(1.0, 'nvidia-smi', (GpuMem(0, 'Test Card A', 1024, 2048, None, None),))
        reader, _backend = chosen(bad)
        assert reader.read(lambda: PIDS) is None
        assert reader.read(lambda: PIDS) is None
        warnings = [message for message in caplog.messages if 'dropped' in message]
        assert warnings == ["GPU memory reading from nvidia-smi dropped: card 0: used_mb is not within 0..total_mb."]

    def test_a_first_sample_that_breaks_a_rule_chooses_the_backend_but_gives_none(self):
        bad = GpuMemoryReading(1.0, 'nvidia-smi', (card(), card()))  # duplicate index
        reader, _created = make_reader(FakeBackend('nvidia-smi', [], samples=[bad]))
        assert reader.read(lambda: PIDS) is None
        assert reader.source == 'nvidia-smi'

    def test_a_valid_reading_is_returned_unchanged(self):
        good = reading(card(0, ours=100), card(1, 'Test Card B', key='pci:0000:0b:00.0'), source='nvidia-smi')
        reader, _backend = chosen(good)
        assert reader.read(lambda: PIDS) is good


class TestStatistics:

    def _stats(self, caplog):
        return [message for message in caplog.messages if message.startswith('GPU memory samples')]

    def test_one_line_every_600_s_with_percentiles(self, caplog):
        caplog.set_level(logging.INFO, logger=READER_LOG)
        clock = Clock()
        log = []
        reader, backend = chosen(clock=clock, duration=0.010, log=log)  # the first sample: 10 ms
        for duration in (0.020, 0.030, 0.040):
            backend.duration = duration
            reader.read(lambda: PIDS)
        assert self._stats(caplog) == []  # not 600 s yet
        clock.now += 600
        backend.duration = 0.250
        reader.read(lambda: PIDS)
        assert self._stats(caplog) == ["GPU memory samples (nvidia-smi): n=5, p50=30.0, p95=250.0, max=250.0"]
        reader.read(lambda: PIDS)
        assert len(self._stats(caplog)) == 1  # at most one line per 600 s

    def test_the_window_keeps_the_last_600_samples(self, caplog):
        caplog.set_level(logging.INFO, logger=READER_LOG)
        clock = Clock()
        reader, backend = chosen(clock=clock, duration=0.001)
        for _ in range(700):
            reader.read(lambda: PIDS)
        clock.now += 600
        reader.read(lambda: PIDS)
        [line] = self._stats(caplog)
        assert line.startswith("GPU memory samples (nvidia-smi): n=600, ")

    def test_no_line_without_a_chosen_backend(self, caplog):
        caplog.set_level(logging.INFO, logger=READER_LOG)
        clock = Clock()
        reader, _created = make_reader(FakeBackend('nvidia-smi', [], samples=[None], clock=clock, duration=1.0),
                                       clock=clock)
        for _ in range(3):
            reader.read(lambda: PIDS)
            clock.now += 601
        assert self._stats(caplog) == []


class TestClose:

    def test_close_waits_for_a_running_sample(self):
        log = []
        reader, backend = chosen(log=log)
        backend.block, backend.entered = threading.Event(), threading.Event()
        sampling = threading.Thread(target=reader.read, args=(lambda: PIDS,), name='test-sample')
        closing = threading.Thread(target=reader.close, name='test-close')
        sampling.start()
        try:
            assert backend.entered.wait(5)
            closing.start()
            closing.join(0.2)
            assert closing.is_alive() and reader._sample_lock.locked()
            assert ('nvidia-smi', 'close') not in log
        finally:
            backend.block.set()
            sampling.join(5)
            closing.join(5)
        assert not closing.is_alive()
        assert log[-2:] == [('nvidia-smi', 'sample-end'), ('nvidia-smi', 'close')]

    def test_close_forgets_the_selection(self):
        log = []
        reader, _backend = chosen(log=log)
        reader.close()
        assert reader.source == '' and reader._backend is None
        assert reader.read(lambda: PIDS) is not None  # selects again
        assert [entry for entry in log if entry[1] == 'open'] == [('nvidia-smi', 'open')] * 2

    def test_close_before_any_read_is_harmless(self):
        reader, _created = make_reader(FakeBackend('nvidia-smi', []))
        reader.close()
        assert reader.source == ''


@pytest.fixture()
def no_singleton():
    reader_module.reset_for_tests()
    yield
    reader_module.reset_for_tests()


class TestSingleton:

    def test_created_on_first_call_and_shared(self, no_singleton):
        assert reader_module._reader is None
        first = reader_module.get_reader()
        assert reader_module.get_reader() is first and first.source == ''

    def test_reset_closes_and_forgets_it(self, no_singleton, monkeypatch):
        first = reader_module.get_reader()
        closed = []
        monkeypatch.setattr(first, 'close', lambda: closed.append(True))
        reader_module.reset_for_tests()
        assert closed == [True] and reader_module._reader is None
        assert reader_module.get_reader() is not first
