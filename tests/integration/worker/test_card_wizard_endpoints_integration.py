# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Setup wizard graphics card step through the real WSGI app and config store (FR-CW-2, FR-CW-5, C-22).

``GET /api/setup/worker/cards/`` and ``POST /api/setup/worker/cards/`` run
the real setup gate, routes, setup mutation lock, ``cards_view``,
``cards.choice`` and ``cards.store``; the saved section lands in a real
``config.json`` in a temp data dir. Only the OS probe and the cached Blender
list are faked (``_card_pages_harness``).
"""

import pytest

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.web_ui import cards_view
from sethlans_worker_agent.web_ui.setup import gate
from sethlans_worker_agent.web_ui.setup.lock import setup_mutation_lock

from . import _card_pages_harness as pages
from ._card_pages_harness import ADDRESS_A, ADDRESS_ABSENT, ADDRESS_B, ADDRESS_C, KEY_A, KEY_B, KEY_C, saved_record

STATUS_PATH = '/api/setup/status/'
LOCK_TEXT = "Setup mutation in progress; retry after current operation completes."


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    pages.reset_wizard_state()
    yield pages.install(monkeypatch, tmp_path)
    pages.reset_wizard_state()


def _checkpoints():
    status, answer = pages.call(STATUS_PATH)
    assert status == '200 OK' and answer['complete'] is False
    return answer['checkpoints']


class TestCardWizardGetIntegration:

    def test_get_lists_fake_cards_with_backends_and_default_choice(self, data_dir):
        status, answer = pages.call(pages.SETUP_PATH)
        assert status == '200 OK'
        assert (answer['method'], answer['gpu_mode'], answer['ticked'], answer['revision']) == ('cpu', 'split', [], 0)
        assert answer['env_override'] is False and answer['blender_check'] == 'done'
        assert answer['blender_version'] == '4.5.1' and answer['config_read_failed'] is False
        assert [w['code'] for w in answer['warnings']] == ['engine_not_tied']  # method CPU with cards (D22)
        assert all(card['state'] == 'unticked' for card in answer['cards'])
        assert (answer['alerts'], answer['missing'], answer['problem']) == ([], [], '')
        assert [n['kind'] for n in answer['notices']] == ['card_new'] * 3  # nothing was shown and saved yet
        assert [card['address'] for card in answer['cards']] == [ADDRESS_A, ADDRESS_B, ADDRESS_C]
        first = answer['cards'][0]
        assert (first['name'], first['pci'], first['backend'], first['family']) == ('Card A', '01:00.0', 'CUDA', 'CUDA')
        assert all(card['note'] == '' and card['ticked'] is False for card in answer['cards'])
        assert pages.config_json(data_dir) is None  # a GET writes nothing

    def test_get_reports_the_saved_section_from_config_json(self, data_dir):
        pages.seed(data_dir, {'method': 'cpu_gpu', 'gpu_mode': 'split', 'cards': [saved_record(KEY_B)],
                              'known': [KEY_A, KEY_B, KEY_C], 'revision': 7})
        _status, answer = pages.call(pages.SETUP_PATH)
        assert (answer['method'], answer['ticked'], answer['revision']) == ('cpu_gpu', [KEY_B], 7)
        assert [card['ticked'] for card in answer['cards']] == [False, True, False]

    def test_get_before_the_blender_check_says_not_checked(self, data_dir, monkeypatch):
        from sethlans_worker_agent.cards import startup_log
        monkeypatch.setattr(startup_log, 'cached_blender_list', lambda: (None, None))
        _status, answer = pages.call(pages.SETUP_PATH)
        assert answer['blender_check'] == 'not_checked'
        assert {card['note'] for card in answer['cards']} == {cards_view.NOTE_NOT_CHECKED}
        assert {card['backend'] for card in answer['cards']} == {None}


class TestCardWizardPostIntegration:

    def test_post_saves_compute_section_and_records_checkpoint(self, data_dir):
        status, answer = pages.call(pages.SETUP_PATH, 'POST', pages.body())
        assert status == '200 OK'
        assert answer['status'] == 'ok' and answer['revision'] == 1 and 'cards_chosen' in answer['checkpoints']
        compute = pages.config_json(data_dir)['compute']
        assert compute['method'] == 'gpu' and compute['gpu_mode'] == 'split'
        assert compute['cards'] == [saved_record(KEY_A), saved_record(KEY_C)]  # PCI address order
        assert compute['known'] == [KEY_A, KEY_C]  # shown ∩ inventory: no B (not shown), no absent address
        assert compute['revision'] == 1
        assert 'cards_chosen' in _checkpoints()

    def test_second_save_on_the_new_revision_raises_it_again(self, data_dir):
        assert pages.call(pages.SETUP_PATH, 'POST', pages.body())[0] == '200 OK'
        status, answer = pages.call(pages.SETUP_PATH, 'POST', pages.body(method='cpu', cards=[], revision=1))
        assert status == '200 OK' and answer['revision'] == 2
        compute = pages.config_json(data_dir)['compute']
        assert (compute['method'], compute['cards'], compute['revision']) == ('cpu', [], 2)
        assert _checkpoints().count('cards_chosen') == 1

    def test_combined_with_two_cuda_cards_is_kept_and_with_one_saved_as_split(self, data_dir):
        assert pages.call(pages.SETUP_PATH, 'POST', pages.body(gpu_mode='combined'))[0] == '200 OK'
        assert pages.config_json(data_dir)['compute']['gpu_mode'] == 'combined'
        one = pages.body(gpu_mode='combined', cards=[ADDRESS_A], revision=1)
        assert pages.call(pages.SETUP_PATH, 'POST', one)[0] == '200 OK'
        assert pages.config_json(data_dir)['compute']['gpu_mode'] == 'split'

    def test_post_while_the_setup_mutation_lock_is_held_is_409(self, data_dir):
        with setup_mutation_lock() as acquired:
            assert acquired
            status, answer = pages.call(pages.SETUP_PATH, 'POST', pages.body())
        assert (status, answer['error']) == ('409 Conflict', LOCK_TEXT)
        assert pages.config_json(data_dir) is None
        assert 'cards_chosen' not in _checkpoints()

    def test_get_and_post_are_404_after_setup_is_complete(self, data_dir):
        gate.mark_setup_complete()
        assert pages.call(pages.SETUP_PATH)[0] == '404 Not Found'
        status, answer = pages.call(pages.SETUP_PATH, 'POST', pages.body())
        assert (status, answer) == ('404 Not Found', {'error': 'Not Found'})
        assert pages.config_json(data_dir) is None

    def test_env_override_is_409_and_writes_nothing(self, data_dir, monkeypatch):
        monkeypatch.setenv(config_compute.ENV_METHOD, 'cpu')
        status, answer = pages.call(pages.SETUP_PATH, 'POST', pages.body())
        assert (status, answer['error']) == ('409 Conflict', cards_view.ENV_OVERRIDE_TEXT)
        assert pages.config_json(data_dir) is None
        assert 'cards_chosen' not in _checkpoints()
        assert pages.call(pages.SETUP_PATH)[1]['env_override'] is True


_INVALID = [
    pytest.param(None, b'{not json', 'The request body must be a JSON object.', id='not-json'),
    pytest.param({'allow_manager_changes': True}, None, 'The request has a field this page does not accept.',
                 id='unknown-field'),
    pytest.param({'revision': True}, None, 'The revision must be a whole number from 0 to 2147483647.',
                 id='bool-revision'),
    pytest.param({'shown': [ADDRESS_A]}, None, 'Every ticked graphics card must also be in the shown list.',
                 id='ticked-not-shown'),
    pytest.param({'cards': [ADDRESS_ABSENT]}, None, 'The graphics card pci:0000:09:00.0 is not on this machine.',
                 id='card-not-on-machine'),
    pytest.param({'cards': []}, None, 'GPU rendering needs at least one ticked graphics card.', id='gpu-no-card'),
    pytest.param({'cards': ['pci:0000:01:00.0']}, None,
                 'Each entry in the cards list must be a PCI address such as 0000:01:00.0.', id='key-not-address'),
]


class TestCardWizardValidationIntegration:

    @pytest.mark.parametrize('change, raw, sentence', _INVALID)
    def test_invalid_save_is_400_and_writes_nothing(self, data_dir, change, raw, sentence):
        pages.seed(data_dir, {'revision': 0})
        before = pages.raw_bytes(data_dir)
        status, answer = pages.call(pages.SETUP_PATH, 'POST', pages.body(**(change or {})), raw=raw)
        assert (status, answer) == ('400 Bad Request', {'error': sentence})
        assert pages.raw_bytes(data_dir) == before
        assert 'cards_chosen' not in _checkpoints()

    def test_oversized_body_is_413(self, data_dir):
        status, answer = pages.call(pages.SETUP_PATH, 'POST', raw=b'x' * 5000)
        assert (status, answer['error']) == ('413 Request Entity Too Large', 'Request body too large')
        assert pages.config_json(data_dir) is None
