# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The card GET answer (``cards_view.view``) shared by both card pages.

Covers the ``blender_check`` states (C-22c), the two distinct Blender notes
(C-19), the backend family, the label address, the probe-failed flag and a
ticked card that is missing now. Inventories are built by the real
``inventory.build`` from fake OS cards, with the platform named, so the
notes are the ones the worker really produces.
"""
import pytest

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.cards import inventory
from sethlans_worker_agent.cards.os_probe import OsCard
from sethlans_worker_agent.web_ui import cards_view
from sethlans_worker_agent.web_ui.setup import blender_devices
from tests.unit.worker._card_choice import (
    AMD, KEY_RTX_A, KEY_XT_23, XT_23_ID, card, inv, rtx_a, section, xt_03, xt_23,
)
from tests.unit.worker._card_pages import SETUP_PATH, call, fake_probes, fake_saved

NOT_CHECKED = 'Blender not checked yet'
CHECK_FAILED = 'Blender could not list its devices; see the log'
OS_XT_23 = OsCard(key=KEY_XT_23, name='AMD Radeon RX 7900 XT', vendor_id=AMD, device_id=0x744C)
EMPTY = config_compute.ComputeSection()


def _view(blender_cards, cards=None, checking=False, sect=EMPTY, **kwargs):
    """The answer for the one OS card ``OS_XT_23`` joined with ``blender_cards``, or for ``cards`` as given."""
    if cards is None:
        built = inventory.build((OS_XT_23,), blender_cards, '4.5.0', platform_name='linux')
    else:
        built = inventory.Inventory(cards=cards, **kwargs)
    return cards_view.view(cards_view.Probe(built, blender_cards), sect, checking=checking)


class TestBlenderCheck:

    @pytest.mark.parametrize('blender_cards, checking, state', [
        (None, False, 'not_checked'),
        (None, True, 'running'),
        (inventory.BLENDER_CHECK_FAILED, False, 'failed'),
        (inventory.BLENDER_CHECK_FAILED, True, 'failed'),
        ([], False, 'done'),
        ([{'id': XT_23_ID, 'type': 'HIP'}], True, 'done'),
    ], ids=['not-checked', 'running', 'failed', 'failed-while-running', 'done-empty', 'done'])
    def test_state(self, blender_cards, checking, state):
        assert _view(blender_cards, checking=checking)['blender_check'] == state

    def test_setup_get_reports_running_check(self, monkeypatch):
        fake_saved(monkeypatch)
        fake_probes(monkeypatch, blender_cards=None)
        monkeypatch.setattr(blender_devices, 'check_state', lambda: 'running')
        assert call(SETUP_PATH)[1]['blender_check'] == 'running'


class TestNotes:

    def test_not_checked_note_replaces_not_installed(self):
        [only] = _view(None)['cards']
        assert only['note'] == NOT_CHECKED

    def test_failed_check_keeps_its_own_note(self):
        [only] = _view(inventory.BLENDER_CHECK_FAILED)['cards']
        assert only['note'] == CHECK_FAILED
        assert only['backend'] is None and only['family'] is None

    def test_done_check_without_the_card_says_not_offered(self):
        [only] = _view([])['cards']
        assert only['note'] == 'not offered by Blender 4.5.0'

    def test_done_check_with_the_card_has_no_note(self):
        [only] = _view([{'id': XT_23_ID, 'type': 'HIP', 'name': 'x'}])['cards']
        assert (only['note'], only['backend'], only['family']) == ('', 'HIP', 'HIP')

    def test_failed_check_on_macos_keeps_apple_gpu(self):
        os_gpu = OsCard(key=None, name='Apple M2 Max')
        built = inventory.build((os_gpu,), inventory.BLENDER_CHECK_FAILED, None, platform_name='darwin')
        answer = cards_view.view(cards_view.Probe(built, inventory.BLENDER_CHECK_FAILED), EMPTY)
        assert [(c['key'], c['note']) for c in answer['cards']] == [('apple-gpu', CHECK_FAILED)]


class TestCardFields:

    def test_optix_card_is_in_the_cuda_family(self):
        [entry] = _view([], cards=(rtx_a(),))['cards']
        assert (entry['backend'], entry['family']) == ('OPTIX', 'CUDA')

    def test_card_fields(self):
        [entry] = _view([], cards=(xt_23(),))['cards']
        assert entry == {
            'key': KEY_XT_23, 'address': '0000:23:00.0', 'pci': '23:00.0', 'name': 'AMD Radeon RX 7900 XT',
            'vendor_id': AMD, 'device_id': 0x744C, 'backend': 'HIP', 'family': 'HIP', 'note': '',
            'ticked': False, 'screen': None, 'primary_screen': None,
        }

    def test_apple_gpu_has_no_pci_label(self):
        apple = card('apple-gpu', 'Apple M2 Max', 'METAL_Apple M2 Max', 'METAL')
        [entry] = _view([], cards=(apple,))['cards']
        assert (entry['address'], entry['pci'], entry['family']) == ('apple-gpu', None, 'METAL')

    def test_long_domain_label_shows_bus_device_function(self):
        [entry] = _view([], cards=(card('pci:0001000a:41:1f.3', 'Card'),))['cards']
        assert (entry['address'], entry['pci']) == ('0001000a:41:1f.3', '41:1f.3')

    @pytest.mark.parametrize('failed', [True, False])
    def test_os_probe_failed_is_reported(self, failed):
        assert _view([], cards=(xt_23(),), os_probe_failed=failed)['os_probe_failed'] is failed


class TestSavedChoice:

    def test_ticked_card_missing_now_is_in_ticked_not_in_cards(self):
        saved = section('gpu', 'split', [KEY_RTX_A, KEY_XT_23], inv(rtx_a(), xt_23()))
        answer = _view([], cards=(xt_03(), xt_23()), sect=saved)
        assert answer['ticked'] == [KEY_RTX_A, KEY_XT_23]
        assert [c['key'] for c in answer['cards']] == ['pci:0000:03:00.0', KEY_XT_23]
        assert [c['ticked'] for c in answer['cards']] == [False, True]

    def test_saved_fields_and_warnings_list(self):
        saved = config_compute.parse({'method': 'cpu_gpu', 'gpu_mode': 'combined', 'revision': 12}, {})
        answer = _view([], cards=(xt_23(),), sect=saved)
        assert (answer['method'], answer['gpu_mode'], answer['revision']) == ('cpu_gpu', 'combined', 12)
        assert answer['env_override'] is False and answer['blender_version'] is None
        assert isinstance(answer['warnings'], list) and answer['warnings'] == []

    def test_env_override_is_reported(self):
        saved = config_compute.parse(None, {config_compute.ENV_METHOD: 'gpu'})
        assert _view([], cards=(), sect=saved)['env_override'] is True
