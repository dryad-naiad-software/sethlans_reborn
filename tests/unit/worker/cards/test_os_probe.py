# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.os_probe`` (FR-CI-5): platform choice, ``None`` for a
failed probe versus ``()`` for an empty one, key order, the 16-card cap, never
raising, and the once-only log.
"""
import logging

import pytest

from sethlans_worker_agent.cards import os_probe, os_probe_linux, os_probe_macos, os_probe_windows
from sethlans_worker_agent.cards.os_probe import OsCard
from tests.unit.worker.cards._card_ids import KEY_5090, KEY_IGPU, KEY_XT_03, KEY_XT_23, os_card, records

LOGGER = 'sethlans_worker_agent.cards.os_probe'


def _patch_platforms(mocker, result=None, side_effect=None):
    return {
        'win32': mocker.patch.object(os_probe_windows, 'probe', return_value=result, side_effect=side_effect),
        'linux': mocker.patch.object(os_probe_linux, 'probe', return_value=result, side_effect=side_effect),
        'darwin': mocker.patch.object(os_probe_macos, 'probe', return_value=result, side_effect=side_effect),
    }


class TestPlatformChoice:

    @pytest.mark.parametrize('platform_name, module', [
        ('win32', 'win32'), ('linux', 'linux'), ('linux2', 'linux'), ('darwin', 'darwin'),
    ])
    def test_picks_the_platform_module(self, mocker, platform_name, module):
        probes = _patch_platforms(mocker, result=())

        assert os_probe.probe(platform_name=platform_name) == ()

        for name, mock in probes.items():
            assert mock.call_count == (1 if name == module else 0)

    def test_unknown_platform_is_none_and_warns_once(self, mocker, caplog):
        probes = _patch_platforms(mocker, result=())
        caplog.set_level(logging.DEBUG)

        assert os_probe.probe(platform_name='freebsd14') is None
        assert os_probe.probe(platform_name='freebsd14') is None

        assert all(mock.call_count == 0 for mock in probes.values())
        warnings = records(caplog, LOGGER, 'WARNING')
        assert [record.getMessage() for record in warnings] == [
            "Graphics cards cannot be listed on platform 'freebsd14'.",
            'Could not list the graphics cards through the operating system.',
        ]

    def test_default_platform_is_sys_platform(self, mocker):
        mocker.patch.object(os_probe, 'sys').platform = 'darwin'
        probes = _patch_platforms(mocker, result=())

        os_probe.probe()

        assert probes['darwin'].call_count == 1


class TestFailedVersusEmpty:

    def test_empty_probe_is_empty_tuple_not_none(self, mocker):
        _patch_platforms(mocker, result=())

        result = os_probe.probe(platform_name='win32')

        assert result == ()
        assert result is not None

    def test_failed_probe_is_none_with_one_warning(self, mocker, caplog):
        _patch_platforms(mocker, result=None)
        caplog.set_level(logging.DEBUG)

        assert os_probe.probe(platform_name='linux') is None
        assert os_probe.probe(platform_name='linux') is None

        warnings = records(caplog, LOGGER, 'WARNING')
        assert [record.getMessage() for record in warnings] == [
            'Could not list the graphics cards through the operating system.']

    @pytest.mark.parametrize('error', [RuntimeError('boom'), OSError(5, 'denied'), KeyError('x'), ValueError()])
    def test_exception_is_none_and_one_log_line(self, mocker, caplog, error):
        _patch_platforms(mocker, side_effect=error)
        caplog.set_level(logging.DEBUG)

        assert os_probe.probe(platform_name='win32') is None
        assert os_probe.probe(platform_name='win32') is None

        logged = records(caplog, LOGGER)
        assert len(logged) == 1
        assert logged[0].levelname == 'WARNING'
        assert logged[0].getMessage() == 'Listing the graphics cards through the operating system failed.'
        assert logged[0].exc_info is not None


class TestOrderAndCap:

    def test_cards_come_back_in_key_order(self, mocker):
        listed = (os_card(KEY_IGPU, 'iGPU', dxgi_index=0), os_card(KEY_XT_23, 'b', dxgi_index=1),
                  os_card(KEY_5090, 'c', dxgi_index=2), os_card(KEY_XT_03, 'd', dxgi_index=3))
        _patch_platforms(mocker, result=listed)

        result = os_probe.probe(platform_name='win32')

        assert [card.key for card in result] == [KEY_5090, KEY_XT_03, KEY_XT_23, KEY_IGPU]
        assert [card.dxgi_index for card in result] == [2, 3, 1, 0]   # each card keeps its own index

    def test_list_input_comes_back_as_tuple(self, mocker):
        _patch_platforms(mocker, result=[os_card(KEY_5090, 'a')])

        assert isinstance(os_probe.probe(platform_name='linux'), tuple)

    def test_macos_cards_without_keys_keep_their_order(self, mocker):
        listed = (OsCard(key=None, name='GPU B'), OsCard(key=None, name='GPU A'))
        _patch_platforms(mocker, result=listed)

        assert os_probe.probe(platform_name='darwin') == listed

    def test_more_than_sixteen_cards_keeps_the_first_sixteen_in_key_order(self, mocker, caplog):
        listed = tuple(os_card(f'pci:0000:{bus:02x}:00.0', f'card {bus}') for bus in range(20, 0, -1))
        _patch_platforms(mocker, result=listed)
        caplog.set_level(logging.DEBUG)

        result = os_probe.probe(platform_name='linux')
        os_probe.probe(platform_name='linux')

        assert len(result) == os_probe.MAX_CARDS == 16
        assert [card.key for card in result] == [f'pci:0000:{bus:02x}:00.0' for bus in range(1, 17)]
        warnings = records(caplog, LOGGER, 'WARNING')
        assert [record.getMessage() for record in warnings] == [
            'The operating system lists 20 graphics cards; only the first 16 are used.']

    def test_exactly_sixteen_cards_logs_nothing(self, mocker, caplog):
        listed = tuple(os_card(f'pci:0000:{bus:02x}:00.0', 'x') for bus in range(16))
        _patch_platforms(mocker, result=listed)
        caplog.set_level(logging.DEBUG)

        assert len(os_probe.probe(platform_name='linux')) == 16
        assert records(caplog, LOGGER) == []


class TestLogOnce:

    def test_same_text_is_logged_once(self, caplog):
        log = logging.getLogger('test.cards.log_once')
        caplog.set_level(logging.DEBUG)

        os_probe.log_once(log, logging.ERROR, 'card %s failed', 'pci:0000:01:00.0')
        os_probe.log_once(log, logging.ERROR, 'card %s failed', 'pci:0000:01:00.0')
        os_probe.log_once(log, logging.ERROR, 'card %s failed', 'pci:0000:23:00.0')

        assert [record.getMessage() for record in records(caplog, 'test.cards.log_once')] == [
            'card pci:0000:01:00.0 failed', 'card pci:0000:23:00.0 failed']

    def test_reset_allows_logging_again(self, caplog):
        log = logging.getLogger('test.cards.log_once')
        caplog.set_level(logging.DEBUG)

        os_probe.log_once(log, logging.WARNING, 'once')
        os_probe.reset_logged_for_tests()
        os_probe.log_once(log, logging.WARNING, 'once')

        assert len(records(caplog, 'test.cards.log_once')) == 2

    def test_message_without_args_is_not_formatted(self, caplog):
        log = logging.getLogger('test.cards.log_once')
        caplog.set_level(logging.DEBUG)

        os_probe.log_once(log, logging.INFO, '100% done')

        assert records(caplog, 'test.cards.log_once')[0].getMessage() == '100% done'

    def test_record_stops_growing_at_its_cap(self, mocker):
        log = mocker.Mock()

        for number in range(os_probe._MAX_REMEMBERED + 10):
            os_probe.log_once(log, logging.INFO, 'line %d', number)
        os_probe.log_once(log, logging.INFO, 'line %d', os_probe._MAX_REMEMBERED + 5)

        assert len(os_probe._logged) == os_probe._MAX_REMEMBERED
        # beyond the cap a line is not remembered, so it is logged again rather than lost
        assert log.log.call_count == os_probe._MAX_REMEMBERED + 11
