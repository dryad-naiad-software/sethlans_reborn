# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""GPU memory reader: backend selection (GPU memory reader FR-GR-3, FR-GS-1..5)."""
import logging

import pytest

from sethlans_worker_agent import config_gpu_memory
from sethlans_worker_agent.gpu_memory import nvidia_smi
from sethlans_worker_agent.gpu_memory import reader as reader_module
from sethlans_worker_agent.gpu_memory.types import GpuMemoryReading
from tests.unit.worker.gpu_memory._reader_fakes import PIDS, Clock, FakeBackend, card, make_reader, only, reading

READER_LOG = 'sethlans_worker_agent.gpu_memory.reader'


class TestCandidates:

    @pytest.mark.parametrize('platform,expected', [
        ('win32', ('pdh', 'nvidia-smi')), ('linux', ('linux-drm', 'nvidia-smi')), ('linux2', ('linux-drm', 'nvidia-smi')),
        ('darwin', ()), ('freebsd14', ())])
    def test_auto_per_platform(self, platform, expected):
        assert reader_module.candidates('auto', platform, is_64bit=True) == expected

    def test_auto_on_32_bit_windows_has_only_nvidia_smi(self):
        assert reader_module.candidates('auto', 'win32', is_64bit=False) == ('nvidia-smi',)

    def test_auto_on_windows_uses_this_python_width(self, monkeypatch):
        monkeypatch.setattr(reader_module.sys, 'maxsize', 2 ** 31 - 1)
        assert reader_module.candidates('auto', 'win32') == ('nvidia-smi',)
        monkeypatch.setattr(reader_module.sys, 'maxsize', 2 ** 63 - 1)
        assert reader_module.candidates('auto', 'win32') == ('pdh', 'nvidia-smi')

    def test_auto_defaults_to_this_platform(self, monkeypatch):
        monkeypatch.setattr(reader_module.sys, 'platform', 'darwin')
        assert reader_module.candidates('auto') == ()

    @pytest.mark.parametrize('platform', ['win32', 'linux', 'darwin'])
    def test_a_pinned_source_is_the_only_candidate_anywhere(self, platform):
        assert reader_module.candidates('nvidia-smi', platform) == ('nvidia-smi',)
        assert reader_module.candidates('amdgpu-sysfs', platform) == ('amdgpu-sysfs',)

    @pytest.mark.parametrize('platform', ['win32', 'linux', 'darwin'])
    def test_none_has_no_candidate(self, platform):
        assert reader_module.candidates('none', platform) == ()

    def test_default_factories_offer_every_backend(self):
        factories = reader_module._default_factories()
        assert set(factories) == {'nvidia-smi', 'pdh', 'linux-drm', 'amdgpu-sysfs'}
        assert factories['nvidia-smi'] is nvidia_smi.NvidiaSmiBackend

    @pytest.mark.parametrize('name,amd_only', [('linux-drm', False), ('amdgpu-sysfs', True)])
    def test_the_linux_factories_build_the_linux_backend_without_opening_it(self, mocker, name, amd_only):
        from sethlans_worker_agent.gpu_memory import linux_backend
        probe = mocker.patch('sethlans_worker_agent.cards.os_probe_linux.probe')
        backend = reader_module._default_factories()[name]()
        assert isinstance(backend, linux_backend.LinuxDrmBackend) and backend.name == name
        assert backend._amd_only is amd_only and not probe.called

    def test_the_pdh_factory_builds_the_windows_backend_without_opening_it(self):
        from sethlans_worker_agent.gpu_memory import win_backend
        backend = reader_module._default_factories()['pdh']()
        assert isinstance(backend, win_backend.PdhBackend) and backend.name == 'pdh'


class TestSelection:

    def test_auto_chooses_the_platform_backend_and_returns_its_first_sample(self):
        log = []
        backend = FakeBackend('nvidia-smi', log)
        reader, created = make_reader(backend, platform='win32')
        assert reader.source == ''
        assert reader.read(lambda: PIDS) == reading(source='nvidia-smi')
        assert reader.source == 'nvidia-smi' and created == ['nvidia-smi']
        assert log == [('nvidia-smi', 'open'), ('nvidia-smi', 'sample', PIDS), ('nvidia-smi', 'sample-end')]

    def test_auto_on_windows_chooses_pdh_before_nvidia_smi(self, monkeypatch):
        monkeypatch.setattr(reader_module.sys, 'maxsize', 2 ** 63 - 1)
        log = []
        reader, created = make_reader(FakeBackend('pdh', log), FakeBackend('nvidia-smi', log), platform='win32')
        assert reader.read(lambda: PIDS).source == 'pdh'
        assert reader.source == 'pdh' and created == ['pdh']

    def test_auto_on_windows_falls_back_to_nvidia_smi_when_pdh_cannot_open(self, monkeypatch):
        monkeypatch.setattr(reader_module.sys, 'maxsize', 2 ** 63 - 1)
        log = []
        reader, created = make_reader(FakeBackend('pdh', log, opens=False), FakeBackend('nvidia-smi', log),
                                      platform='win32')
        assert reader.read(lambda: PIDS).source == 'nvidia-smi'
        assert created == ['pdh', 'nvidia-smi'] and ('pdh', 'close') in log

    def test_auto_on_macos_tries_nothing(self):
        backend = FakeBackend('nvidia-smi', [])
        reader, created = make_reader(backend, platform='darwin')
        assert reader.read(lambda: PIDS) is None
        assert reader.source == 'none' and created == []

    def test_pinned_source_runs_on_any_platform(self):
        reader, created = make_reader(FakeBackend('nvidia-smi', []), setting='nvidia-smi', platform='darwin')
        assert reader.read(lambda: PIDS) is not None and reader.source == 'nvidia-smi' and created == ['nvidia-smi']

    def test_pinned_source_without_a_backend_here_selects_nothing(self, caplog):
        caplog.set_level(logging.INFO, logger=READER_LOG)
        reader, created = make_reader(FakeBackend('nvidia-smi', []), setting='pdh', platform='win32')
        assert reader.read(lambda: PIDS) is None
        assert reader.source == 'none' and created == []  # the other backend is never tried
        assert "GPU memory source pdh not chosen: not available on this worker." in caplog.messages

    def test_none_selects_nothing_and_never_builds_a_backend(self):
        clock = Clock()
        reader, created = make_reader(FakeBackend('nvidia-smi', []), setting='none', platform='linux', clock=clock)
        assert reader.read(lambda: PIDS) is None
        assert reader.source == 'none' and created == [] and reader._next_select == clock.now + 600

    @pytest.mark.parametrize('configured,chosen', [('none', 'none'), ('auto', 'nvidia-smi'), ('nvidia-smi', 'nvidia-smi')])
    def test_without_a_setting_the_config_value_is_used(self, monkeypatch, configured, chosen):
        monkeypatch.setattr(config_gpu_memory, 'SOURCE', configured)
        reader, _created = make_reader(FakeBackend('nvidia-smi', []), setting=None, platform='linux')
        reader.read(lambda: PIDS)
        assert reader.source == chosen

    def test_a_candidate_that_does_not_open_is_closed_and_the_next_is_tried(self, monkeypatch, caplog):
        caplog.set_level(logging.INFO, logger=READER_LOG)
        log = []
        only(monkeypatch, 'fake-a', 'fake-b')
        reader, created = make_reader(FakeBackend('fake-a', log, opens=False), FakeBackend('fake-b', log))
        assert reader.read(lambda: PIDS).source == 'fake-b'
        assert created == ['fake-a', 'fake-b'] and reader.source == 'fake-b'
        assert log[:2] == [('fake-a', 'open'), ('fake-a', 'close')]
        assert "GPU memory source fake-a not chosen: it cannot be used here." in caplog.messages

    @pytest.mark.parametrize('first', [None, GpuMemoryReading(0.0, 'fake-a', ())])
    def test_a_first_sample_without_a_card_rejects_the_candidate(self, monkeypatch, caplog, first):
        caplog.set_level(logging.INFO, logger=READER_LOG)
        log = []
        only(monkeypatch, 'fake-a', 'fake-b')
        reader, _created = make_reader(FakeBackend('fake-a', log, samples=[first]), FakeBackend('fake-b', log))
        assert reader.read(lambda: PIDS).source == 'fake-b'
        assert ('fake-a', 'close') in log and ('fake-b', 'close') not in log
        assert "GPU memory source fake-a not chosen: its first sample has no card." in caplog.messages

    @pytest.mark.parametrize('where', ['open', 'sample'])
    def test_a_candidate_that_raises_is_rejected_and_closed(self, monkeypatch, caplog, where):
        caplog.set_level(logging.INFO, logger=READER_LOG)
        log = []
        only(monkeypatch, 'fake-a', 'fake-b')
        broken = FakeBackend('fake-a', log, opens=OSError('gone') if where == 'open' else True,
                             samples=[RuntimeError('boom')] if where == 'sample' else None)
        reader, _created = make_reader(broken, FakeBackend('fake-b', log))
        assert reader.read(lambda: PIDS).source == 'fake-b'
        assert ('fake-a', 'close') in log
        reason = "OSError" if where == 'open' else "RuntimeError"
        assert f"GPU memory source fake-a not chosen: it failed ({reason})." in caplog.messages

    def test_a_factory_that_raises_is_rejected(self, monkeypatch):
        only(monkeypatch, 'fake-a', 'fake-b')

        def broken():
            raise ImportError('no module')

        reader = reader_module.GpuMemoryReader(setting='auto', factories={
            'fake-a': broken, 'fake-b': lambda: FakeBackend('fake-b', [])}, clock=Clock())
        assert reader.read(lambda: PIDS).source == 'fake-b'

    def test_the_chosen_backend_is_logged_with_its_cards(self, caplog):
        caplog.set_level(logging.INFO, logger=READER_LOG)
        first = reading(card(0, 'Test Card A', 8192, key='pci:0000:0a:00.0'), card(1, 'Test Card B', 4096, key=None),
                        source='nvidia-smi')
        reader, _created = make_reader(FakeBackend('nvidia-smi', [], samples=[first]))
        reader.read(lambda: PIDS)
        assert ("GPU memory source: nvidia-smi; cards: 0 'Test Card A' 8192 MiB pci:0000:0a:00.0; "
                "1 'Test Card B' 4096 MiB no card key.") in caplog.messages

    def test_a_failure_while_choosing_leaves_no_closed_backend_chosen(self, monkeypatch):
        log = []
        backend = FakeBackend('nvidia-smi', log)
        reader, _created = make_reader(backend)
        monkeypatch.setattr(reader_module, '_card_list', lambda first: 1 / 0)  # the chosen-source log line fails
        assert reader.read(lambda: PIDS) is None
        assert ('nvidia-smi', 'close') in log
        assert reader._backend is None and reader.source == 'none'


class TestChosenOnce:

    def test_a_failed_sample_gives_none_and_never_reselects(self):
        log = []
        backend = FakeBackend('nvidia-smi', log, samples=[reading(source='nvidia-smi'), None, RuntimeError('x'),
                                                          reading(source='nvidia-smi')])
        reader, created = make_reader(backend)
        assert reader.read(lambda: PIDS) is not None
        assert reader.read(lambda: PIDS) is None
        assert reader.read(lambda: PIDS) is None
        assert reader.read(lambda: PIDS) is not None
        assert created == ['nvidia-smi'] and reader.source == 'nvidia-smi'
        assert [entry for entry in log if entry[1] in ('open', 'close')] == [('nvidia-smi', 'open')]

    def test_later_reads_go_straight_to_sample_with_fresh_pids(self):
        log = []
        reader, _created = make_reader(FakeBackend('nvidia-smi', log))
        reader.read(lambda: PIDS)
        reader.read(lambda: frozenset({7}))
        assert log[-2:] == [('nvidia-smi', 'sample', frozenset({7})), ('nvidia-smi', 'sample-end')]


class TestRetry:

    def test_selection_is_retried_only_after_600_s(self):
        clock = Clock()
        backend = FakeBackend('nvidia-smi', [], opens=False)
        reader, created = make_reader(backend, clock=clock)
        assert reader.read(lambda: PIDS) is None and reader.source == 'none' and created == ['nvidia-smi']
        clock.now += 599.9
        assert reader.read(lambda: PIDS) is None and created == ['nvidia-smi']
        backend.opens = True
        clock.now += 0.1
        assert reader.read(lambda: PIDS) is not None
        assert created == ['nvidia-smi', 'nvidia-smi'] and reader.source == 'nvidia-smi'

    def test_each_failed_selection_restarts_the_wait(self):
        clock = Clock()
        reader, created = make_reader(FakeBackend('nvidia-smi', [], opens=False), clock=clock)
        reader.read(lambda: PIDS)
        clock.now += 600
        reader.read(lambda: PIDS)
        assert len(created) == 2 and reader._next_select == clock.now + 600

    def test_the_wait_is_logged(self, caplog):
        caplog.set_level(logging.INFO, logger=READER_LOG)
        reader, _created = make_reader(FakeBackend('nvidia-smi', [], opens=False))
        reader.read(lambda: PIDS)
        assert ("No GPU memory source was chosen (tried: linux-drm, nvidia-smi); the next try is in 600 s."
                in caplog.messages)


class TestFailingClose:

    def test_a_close_that_raises_still_tries_the_next_candidate(self, monkeypatch, caplog):
        log = []
        only(monkeypatch, 'fake-a', 'fake-b')
        reader, _created = make_reader(FakeBackend('fake-a', log, opens=False, close_error=OSError('stuck')),
                                       FakeBackend('fake-b', log))
        assert reader.read(lambda: PIDS).source == 'fake-b' and reader.source == 'fake-b'
        assert "Closing the GPU memory source fake-a failed." in caplog.messages

    def test_a_close_that_raises_still_sets_the_next_selection(self):
        clock = Clock()
        reader, created = make_reader(FakeBackend('nvidia-smi', [], opens=False, close_error=RuntimeError('stuck')),
                                      clock=clock)
        assert reader.read(lambda: PIDS) is None
        assert reader.source == 'none' and reader._next_select == clock.now + 600
        reader.read(lambda: PIDS)
        assert created == ['nvidia-smi']  # no retry before 600 s, despite the failed close

    def test_reader_close_survives_a_backend_close_that_raises(self):
        log = []
        reader, _created = make_reader(FakeBackend('nvidia-smi', log, close_error=OSError('stuck')))
        reader.read(lambda: PIDS)
        reader.close()
        assert reader.source == '' and reader._next_select is None and log[-1] == ('nvidia-smi', 'close')
