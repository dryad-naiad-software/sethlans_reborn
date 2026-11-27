# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The card GET answer (``cards_view.view``) of the settings page.

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
from tests.unit.worker._card_choice import (
    AMD, KEY_RTX_A, KEY_XT_23, XT_23_ID, card, inv, rtx_a, section, xt_03, xt_23,
)

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
            'vendor_id': AMD, 'device_id': 0x744C, 'revision_id': 0xCC, 'backend': 'HIP', 'family': 'HIP', 'note': '',
            'ticked': False, 'state': 'unticked', 'screen': None, 'primary_screen': None,
            'job_id': None, 'renderer': None,
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
        assert [w['code'] for w in answer['warnings']] == ['engine_not_tied']  # no card in use (D22)

    def test_env_override_is_reported(self):
        saved = config_compute.parse(None, {config_compute.ENV_METHOD: 'gpu'})
        assert _view([], cards=(), sect=saved)['env_override'] is True


class TestStates:
    """Chunk 4: states, alerts, notices, problem and warnings, for display only (FR-CW-3, FR-CW-4, S1)."""

    ALERT = {'id': 'a' * 32, 'kind': 'card_changed', 'key': KEY_XT_23, 'name': 'AMD Radeon RX 7900 XT',
             'detail': 'was RX 7900 XT, now RX 7800 XT', 'raised_at': '2026-11-19T08:00:00+00:00'}

    def test_states_missing_card_alerts_notices_and_problem(self):
        saved = section('gpu', 'split', [KEY_RTX_A, KEY_XT_23], inv(rtx_a(), xt_23()), alerts=[self.ALERT])
        answer = _view([], cards=(xt_03(), xt_23()), sect=saved)

        assert [(c['key'], c['state']) for c in answer['cards']] == [('pci:0000:03:00.0', 'unticked'),
                                                                     (KEY_XT_23, 'held')]
        assert answer['missing'] == [{'key': KEY_RTX_A, 'address': '0000:01:00.0', 'pci': '01:00.0',
                                      'name': 'NVIDIA GeForce RTX 4080', 'state': 'lost',
                                      'vendor_id': 0x10DE, 'device_id': 0x2704, 'revision_id': 0xA1,
                                      'job_id': None, 'renderer': None}]
        assert answer['alerts'] == [self.ALERT]
        assert answer['notices'] == [{'kind': 'card_new', 'key': 'pci:0000:03:00.0', 'name': 'AMD Radeon RX 7900 XT'},
                                     {'kind': 'card_new', 'key': KEY_XT_23, 'name': 'AMD Radeon RX 7900 XT'}]
        assert answer['problem'].startswith('None of the ticked graphics cards can be used')
        assert answer['config_read_failed'] is False

    def test_ok_card_in_use_gives_no_warning(self):
        saved = section('gpu', 'split', [KEY_XT_23], inv(xt_23()))
        answer = _view([], cards=(xt_23(),), sect=saved)
        assert (answer['cards'][0]['state'], answer['warnings'], answer['problem']) == ('ok', [], '')

    def test_get_raises_no_alert_and_writes_nothing(self, mocker):
        add_alert = mocker.patch('sethlans_worker_agent.cards.store.add_alert')
        set_many = mocker.patch('sethlans_worker_agent.config_store.set_many')
        saved = section('gpu', 'split', [KEY_RTX_A], inv(rtx_a()))
        assert _view([], cards=(xt_23(),), sect=saved)['missing'][0]['state'] == 'lost'
        add_alert.assert_not_called()
        set_many.assert_not_called()

    def test_failed_read_reports_it_instead_of_defaults(self):
        failed = config_compute.ComputeSection(read_failed=True)
        answer = _view([], cards=(xt_23(),), sect=failed)
        assert answer['config_read_failed'] is True and answer['error'] == cards_view.READ_FAILED_TEXT
        assert (answer['method'], answer['gpu_mode'], answer['revision'], answer['ticked']) == (None, None, None, [])
        assert (answer['alerts'], answer['notices'], answer['warnings'], answer['missing']) == ([], [], [], [])
        assert answer['cards'][0]['state'] is None and answer['cards'][0]['ticked'] is False
