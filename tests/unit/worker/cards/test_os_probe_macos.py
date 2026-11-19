# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.os_probe_macos`` (FR-CI-4) with fake
``system_profiler`` output. ``subprocess.run`` and the temporary file are
replaced, so nothing runs and nothing is written.
"""
import io
import json
import subprocess

import pytest

from sethlans_worker_agent.cards import os_probe_macos
from sethlans_worker_agent.cards.os_probe import OsCard

APPLE_SILICON = {
    'SPDisplaysDataType': [{
        '_name': 'Apple M2 Max',
        'sppci_model': 'Apple M2 Max',
        'sppci_cores': '38',
        'spdisplays_vendor': 'sppci_vendor_Apple',
        'spdisplays_ndrvs': [{'_name': 'Color LCD', 'spdisplays_main': 'spdisplays_yes'}],
    }],
}

INTEL_MAC_TWO_GPUS = {
    'SPDisplaysDataType': [
        {'_name': 'Intel UHD Graphics 630', 'sppci_model': 'Intel UHD Graphics 630'},
        {'_name': 'AMD Radeon Pro 5500M', 'sppci_model': 'AMD Radeon Pro 5500M',
         'spdisplays_ndrvs': [{'_name': 'External'}]},
    ],
}


def _bytes(document):
    return json.dumps(document).encode()


class TestParse:

    def test_apple_silicon_with_a_display(self):
        assert os_probe_macos.parse(_bytes(APPLE_SILICON)) == (
            OsCard(key=None, name='Apple M2 Max', display=True),)

    def test_headless_gpu_has_no_display(self):
        document = {'SPDisplaysDataType': [{'sppci_model': 'Apple M1'}]}

        assert os_probe_macos.parse(_bytes(document)) == (OsCard(key=None, name='Apple M1', display=False),)

    @pytest.mark.parametrize('displays', [[], {}, 'Color LCD', None])
    def test_display_needs_a_non_empty_list(self, displays):
        document = {'SPDisplaysDataType': [{'sppci_model': 'Apple M1', 'spdisplays_ndrvs': displays}]}

        assert os_probe_macos.parse(_bytes(document))[0].display is False

    def test_two_gpus_in_listed_order(self):
        cards = os_probe_macos.parse(_bytes(INTEL_MAC_TWO_GPUS))

        assert [(card.name, card.display, card.key) for card in cards] == [
            ('Intel UHD Graphics 630', False, None), ('AMD Radeon Pro 5500M', True, None)]

    def test_name_falls_back_to_name_field_then_generic(self):
        document = {'SPDisplaysDataType': [{'_name': 'kHW_AppleM3Item'}, {}, {'sppci_model': '\x00'}]}

        assert [card.name for card in os_probe_macos.parse(_bytes(document))] == [
            'kHW_AppleM3Item', 'Apple GPU', 'Apple GPU']

    def test_non_dict_entries_are_skipped(self):
        document = {'SPDisplaysDataType': ['text', 5, None, {'sppci_model': 'Apple M2'}]}

        assert [card.name for card in os_probe_macos.parse(_bytes(document))] == ['Apple M2']

    def test_macos_cards_have_no_revision(self):
        assert os_probe_macos.parse(_bytes(APPLE_SILICON))[0].revision_id is None

    def test_deeply_nested_json_is_none_not_a_crash(self):
        depth = 100_000
        data = b'{"SPDisplaysDataType": ' + b'[' * depth + b']' * depth + b'}'

        assert os_probe_macos.parse(data) is None

    def test_empty_list_is_empty_not_none(self):
        assert os_probe_macos.parse(b'{"SPDisplaysDataType": []}') == ()

    def test_invalid_utf8_is_replaced_not_fatal(self):
        data = b'{"SPDisplaysDataType": [{"sppci_model": "Apple \xff M2"}]}'

        assert os_probe_macos.parse(data)[0].name == 'Apple � M2'

    @pytest.mark.parametrize('data', [
        b'', b'not json', b'[1, 2]', b'"text"', b'{}', b'{"SPDisplaysDataType": {}}',
        b'{"SPDisplaysDataType": null}', b'{"SPDisplaysDataType": [',
    ])
    def test_unreadable_output_is_none(self, data):
        assert os_probe_macos.parse(data) is None


class _FakeRun:
    """Replaces ``subprocess.run`` and ``tempfile.TemporaryFile``."""

    def __init__(self, mocker, output=b'', returncode=0, error=None):
        self.output = output
        self.returncode = returncode
        self.error = error
        self.calls = []
        mocker.patch.object(os_probe_macos.tempfile, 'TemporaryFile', side_effect=io.BytesIO)
        mocker.patch.object(os_probe_macos.subprocess, 'run', side_effect=self.run)

    def run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        kwargs['stdout'].write(self.output)
        return subprocess.CompletedProcess(args, self.returncode)


class TestProbe:

    def test_runs_system_profiler_safely_and_parses(self, mocker):
        fake = _FakeRun(mocker, output=_bytes(APPLE_SILICON))

        cards = os_probe_macos.probe()

        assert [card.name for card in cards] == ['Apple M2 Max']
        args, kwargs = fake.calls[0]
        assert args == ['/usr/sbin/system_profiler', 'SPDisplaysDataType', '-json']
        assert kwargs['shell'] is False
        assert kwargs['timeout'] == 15
        assert kwargs['stdin'] is subprocess.DEVNULL
        assert kwargs['stderr'] is subprocess.DEVNULL

    def test_nonzero_exit_is_none(self, mocker):
        _FakeRun(mocker, output=_bytes(APPLE_SILICON), returncode=1)

        assert os_probe_macos.probe() is None

    @pytest.mark.parametrize('error', [
        FileNotFoundError('/usr/sbin/system_profiler'),
        subprocess.TimeoutExpired(['system_profiler'], 15),
        PermissionError('denied'),
    ])
    def test_run_failure_is_none(self, mocker, error):
        _FakeRun(mocker, error=error)

        assert os_probe_macos.probe() is None

    def test_output_over_one_mib_is_none(self, mocker):
        body = _bytes(APPLE_SILICON)   # valid JSON, padded one byte past the cap
        _FakeRun(mocker, output=body + b' ' * (os_probe_macos.OUTPUT_CAP + 1 - len(body)))

        assert os_probe_macos.probe() is None

    def test_output_of_exactly_one_mib_is_parsed(self, mocker):
        body = _bytes(APPLE_SILICON)
        _FakeRun(mocker, output=body + b' ' * (os_probe_macos.OUTPUT_CAP - len(body)))

        assert len(os_probe_macos.probe()) == 1

    def test_unparsable_output_is_none(self, mocker):
        _FakeRun(mocker, output=b'system_profiler: error')

        assert os_probe_macos.probe() is None
