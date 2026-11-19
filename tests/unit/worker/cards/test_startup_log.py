# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.startup_log``: the joined card list is logged once,
never raises, never starts Blender, and is called from ``init_capacity``.
"""
import logging

import pytest

from sethlans_worker_agent import hardware_detection
from sethlans_worker_agent.cards import inventory, os_probe, startup_log
from sethlans_worker_agent.cards.inventory import Card, Inventory
from tests.unit.worker.cards._card_ids import RX_7900_XT_03, records, two_xt_blender_cards, two_xt_os_cards

LOGGER = 'sethlans_worker_agent.cards.startup_log'
BLENDER_EXE = '/opt/sethlans/blender-4.5.3-linux-x64/blender'


@pytest.fixture
def no_blender_start(mocker):
    """Every way the detection could start Blender, as mocks that must stay uncalled."""
    return [
        mocker.patch.object(hardware_detection, 'get_gpu_device_details'),
        mocker.patch.object(hardware_detection, '_fill_gpu_details_cache'),
        mocker.patch.object(hardware_detection.subprocess, 'run'),
    ]


@pytest.fixture
def pc_platform(mocker):
    """The join as on Windows and Linux, so the expected lines do not depend on the runner's OS."""
    mocker.patch.object(inventory, 'sys').platform = 'linux'


def _cache(monkeypatch, cards, exe):
    monkeypatch.setattr(hardware_detection, '_gpu_details_cache', cards)
    monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', exe)


class TestCachedBlenderList:

    def test_before_detection_is_none_none(self, monkeypatch, no_blender_start):
        _cache(monkeypatch, None, None)

        assert startup_log.cached_blender_list() == (None, None)
        for mock in no_blender_start:
            mock.assert_not_called()

    def test_filled_cache_gives_a_copy_and_the_version(self, monkeypatch, no_blender_start):
        cached = two_xt_blender_cards()
        _cache(monkeypatch, cached, BLENDER_EXE)

        cards, version = startup_log.cached_blender_list()

        assert cards == cached
        assert cards is not cached
        assert version == '4.5.3'
        for mock in no_blender_start:
            mock.assert_not_called()

    def test_empty_cache_is_an_empty_list_not_none(self, monkeypatch):
        _cache(monkeypatch, [], 'C:\\Sethlans\\blender-5.0.1-windows-x64\\blender.exe')

        assert startup_log.cached_blender_list() == ([], '5.0.1')

    def test_empty_cache_without_executable_is_a_failed_check(self, monkeypatch, no_blender_start):
        _cache(monkeypatch, [], None)

        cards, version = startup_log.cached_blender_list()

        assert cards is inventory.BLENDER_CHECK_FAILED
        assert version is None
        for mock in no_blender_start:
            mock.assert_not_called()


class TestLogOnceAtStart:

    def test_logs_the_joined_list_once(self, mocker, monkeypatch, caplog, no_blender_start, pc_platform):
        probe = mocker.patch.object(os_probe, 'probe', return_value=two_xt_os_cards())
        _cache(monkeypatch, two_xt_blender_cards(), BLENDER_EXE)
        caplog.set_level(logging.DEBUG)

        startup_log.log_once_at_start()
        startup_log.log_once_at_start()

        assert probe.call_count == 1
        lines = [record.getMessage() for record in records(caplog, LOGGER, 'INFO')]
        assert lines == [
            'Graphics cards (operating system list, Blender 4.5.3): 2 found. The worker does not use this list yet.',
            "  - pci:0000:03:00.0 'AMD Radeon RX 7900 XT', ids 1002:744c rev c8, DXGI adapter 0, 20464 MiB, "
            "Blender 'HIP' 'HIP_AMD Radeon RX 7900 XT_0000:03:00'",
            "  - pci:0000:23:00.0 'AMD Radeon RX 7900 XT', ids 1002:744c rev cc, DXGI adapter 1, 20464 MiB, "
            "Blender 'HIP' 'HIP_AMD Radeon RX 7900 XT_0000:23:00'",
        ]
        for mock in no_blender_start:
            mock.assert_not_called()

    def test_reset_allows_a_second_log(self, mocker, monkeypatch):
        probe = mocker.patch.object(os_probe, 'probe', return_value=())
        _cache(monkeypatch, None, None)

        startup_log.log_once_at_start()
        startup_log.reset_for_tests()
        startup_log.log_once_at_start()

        assert probe.call_count == 2

    def test_no_blender_yet_never_starts_blender(self, mocker, monkeypatch, caplog, no_blender_start, pc_platform):
        mocker.patch.object(os_probe, 'probe', return_value=two_xt_os_cards())
        _cache(monkeypatch, None, None)
        caplog.set_level(logging.DEBUG)

        startup_log.log_once_at_start()

        lines = [record.getMessage() for record in records(caplog, LOGGER, 'INFO')]
        assert lines[0] == ('Graphics cards (operating system list, no Blender detection yet): 2 found. '
                            'The worker does not use this list yet.')
        assert lines[1].endswith('20464 MiB, Blender not installed yet')
        for mock in no_blender_start:
            mock.assert_not_called()

    def test_failed_probe_is_described(self, mocker, monkeypatch, caplog, pc_platform):
        mocker.patch.object(os_probe, 'probe', return_value=None)
        _cache(monkeypatch, [{'id': RX_7900_XT_03, 'type': 'HIP', 'name': 'AMD Radeon RX 7900 XT'}], BLENDER_EXE)
        caplog.set_level(logging.DEBUG)

        startup_log.log_once_at_start()

        lines = [record.getMessage() for record in records(caplog, LOGGER, 'INFO')]
        assert lines == [
            'Graphics cards (from Blender; the operating system list failed, Blender 4.5.3): 1 found. '
            'The worker does not use this list yet.',
            "  - pci:0000:03:00.0 'AMD Radeon RX 7900 XT', Blender 'HIP' 'HIP_AMD Radeon RX 7900 XT_0000:03:00'",
        ]

    def test_failed_blender_check_is_described(self, mocker, monkeypatch, caplog, no_blender_start, pc_platform):
        mocker.patch.object(os_probe, 'probe', return_value=two_xt_os_cards())
        _cache(monkeypatch, [], None)   # what a failed device check leaves behind
        caplog.set_level(logging.DEBUG)

        startup_log.log_once_at_start()

        lines = [record.getMessage() for record in records(caplog, LOGGER, 'INFO')]
        assert len(lines) == 3
        assert lines[1].endswith('20464 MiB, Blender could not list its devices; see the log')
        assert lines[2].endswith('20464 MiB, Blender could not list its devices; see the log')
        for mock in no_blender_start:
            mock.assert_not_called()

    @pytest.mark.parametrize('cache, exe, expected', [
        ('list', BLENDER_EXE, 'Blender 4.5.3'),                       # detected by a managed Blender
        (None, None, 'no Blender detection yet'),                      # not installed yet
        ([], None, 'the Blender device check failed'),                 # what a failed device check leaves
        ('list', '/usr/bin/blender', 'Blender version unknown'),       # outside the managed folders
        ([], 'C:\\Program Files\\Blender\\blender.exe', 'Blender version unknown'),
    ])
    def test_header_names_the_blender_state(self, mocker, monkeypatch, caplog, pc_platform, cache, exe, expected):
        mocker.patch.object(os_probe, 'probe', return_value=two_xt_os_cards())
        _cache(monkeypatch, two_xt_blender_cards() if cache == 'list' else cache, exe)
        caplog.set_level(logging.DEBUG)

        startup_log.log_once_at_start()

        lines = [record.getMessage() for record in records(caplog, LOGGER, 'INFO')]
        assert lines[0] == (f'Graphics cards (operating system list, {expected}): 2 found. '
                            'The worker does not use this list yet.')
        assert len(lines) == 3

    @pytest.mark.parametrize('target, attribute', [
        (os_probe, 'probe'), (inventory, 'build'), (startup_log, 'cached_blender_list'),
    ])
    def test_never_raises_and_warns(self, mocker, monkeypatch, caplog, target, attribute):
        _cache(monkeypatch, None, None)
        mocker.patch.object(target, attribute, side_effect=RuntimeError('probe exploded'))
        caplog.set_level(logging.DEBUG)

        startup_log.log_once_at_start()
        startup_log.log_once_at_start()

        warnings = records(caplog, LOGGER, 'WARNING')
        assert [record.getMessage() for record in warnings] == ['The graphics card list could not be logged.']
        assert warnings[0].exc_info[0] is RuntimeError

    def test_marked_done_before_work_so_a_failure_is_not_retried(self, mocker, monkeypatch):
        _cache(monkeypatch, None, None)
        probe = mocker.patch.object(os_probe, 'probe', side_effect=RuntimeError('x'))

        startup_log.log_once_at_start()
        startup_log.log_once_at_start()

        assert probe.call_count == 1


class TestDescribe:

    def test_minimal_card(self):
        assert startup_log._describe(Card(key='apple-gpu', name='Apple M2 Max')) == "apple-gpu 'Apple M2 Max'"

    def test_non_ascii_name_and_id_are_escaped(self):
        card = Card(key='pci:0000:79:00.0', name='Radeon\u2122\nGraphics', blender=('HIP_\u00e9_0000:79:00', 'HIP'))

        text = startup_log._describe(card)

        assert text == "pci:0000:79:00.0 'Radeon\\u2122\\nGraphics', Blender 'HIP' 'HIP_\\xe9_0000:79:00'"
        assert text.isascii()

    def test_backend_is_escaped_too(self):
        card = Card(key='pci:0000:79:00.0', name='x', blender=('HIP_x_0000:79:00', 'H\u00ccP\n'))

        text = startup_log._describe(card)

        assert text == "pci:0000:79:00.0 'x', Blender 'H\\xccP\\n' 'HIP_x_0000:79:00'"
        assert text.isascii()

    @pytest.mark.parametrize('revision_id, expected', [(0xC8, 'ids 1002:744c rev c8'), (0, 'ids 1002:744c rev 00'),
                                                       (None, 'ids 1002:744c')])
    def test_revision_follows_the_ids(self, revision_id, expected):
        card = Card(key='pci:0000:03:00.0', name='x', vendor_id=0x1002, device_id=0x744C, revision_id=revision_id)

        assert startup_log._describe(card) == f"pci:0000:03:00.0 'x', {expected}"

    def test_revision_without_ids_is_not_shown(self):
        assert startup_log._describe(Card(key='pci:0000:03:00.0', name='x', revision_id=0xC8)) == \
            "pci:0000:03:00.0 'x'"

    def test_empty_inventory_logs_only_the_header(self, caplog):
        caplog.set_level(logging.DEBUG)

        startup_log._log(Inventory())

        assert [record.getMessage() for record in records(caplog, LOGGER)] == [
            'Graphics cards (operating system list, no Blender detection yet): 0 found. '
            'The worker does not use this list yet.']


class TestInitCapacityCall:

    def test_init_capacity_logs_the_card_list(self, mocker):
        from sethlans_worker_agent import job_processor
        mocker.patch('sethlans_worker_agent.system_monitor.get_gpu_device_details', return_value=[])
        mocker.patch('sethlans_worker_agent.system_monitor.get_cpu_thread_count', return_value=8)
        mocker.patch('sethlans_worker_agent.config.FORCE_CPU_ONLY', False)
        mocker.patch('sethlans_worker_agent.config.FORCE_GPU_ONLY', False)
        mocker.patch('sethlans_worker_agent.config.FORCE_GPU_INDEX', None)
        mocker.patch('sethlans_worker_agent.config.GPU_MODE', 'split')
        mocker.patch('sethlans_worker_agent.config.CPU_THREADS', 0)
        log_card_list = mocker.patch.object(job_processor.cards_startup_log, 'log_once_at_start')

        job_processor.init_capacity()

        log_card_list.assert_called_once_with()
