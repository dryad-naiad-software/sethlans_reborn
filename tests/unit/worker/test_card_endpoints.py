# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Smoke tests for the graphics card endpoints (FR-CW-2, FR-CW-4, FR-CW-5).

The probe is replaced by a fixed inventory and the saved ``compute`` section
lives in a dict (``config_compute._read_raw`` reads it, a fake
``config_store.set_many`` applies each write), so no real card is probed and
no real ``config.json`` is touched.
"""
import pytest

from sethlans_worker_agent import config_compute, config_store
from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.cards import inventory
from sethlans_worker_agent.web_ui import cards_view
from sethlans_worker_agent.web_ui.setup import gate, lock
from tests.unit.worker._card_choice import KEY_XT_03, KEY_XT_23, card, inv
from tests.unit.worker._card_pages import SETUP_PATH, fake_probes, fake_saved
from tests.unit.worker._card_pages import body as _body
from tests.unit.worker._card_pages import call as _call


@pytest.fixture
def saved(monkeypatch):
    return fake_saved(monkeypatch)


@pytest.fixture
def probes(monkeypatch):
    return fake_probes(monkeypatch)


class TestSetup:

    def test_get_lists_cards_and_choice(self, saved, probes):
        status, answer = _call(SETUP_PATH)
        assert status == 200
        assert answer['revision'] == 0 and answer['method'] == 'cpu' and answer['ticked'] == []
        assert [w['code'] for w in answer['warnings']] == ['engine_not_tied']  # method CPU, cards present (D22)
        assert answer['env_override'] is False and answer['config_read_failed'] is False
        assert [c['address'] for c in answer['cards']] == ['0000:03:00.0', '0000:23:00.0']
        assert answer['cards'][1]['pci'] == '23:00.0' and answer['cards'][1]['family'] == 'HIP'

    def test_get_before_blender_check_says_not_checked(self, saved, monkeypatch):
        probe = cards_view.Probe(inv(card(KEY_XT_03, 'XT', note=inventory.NOTE_NO_BLENDER)), None)
        monkeypatch.setattr(cards_view, 'probe_now', lambda: probe)
        _status, answer = _call(SETUP_PATH)
        assert answer['cards'][0]['note'] == 'Blender not checked yet'
        assert answer['blender_check'] == 'not_checked'

    def test_post_saves_and_records_checkpoint(self, saved, probes, monkeypatch, tmp_path):
        monkeypatch.setattr(config_store, 'get_data_dir', lambda: tmp_path)  # no sentinel
        status, answer = _call(SETUP_PATH, 'POST', _body())
        assert status == 200 and answer['revision'] == 1
        assert 'cards_chosen' in answer['checkpoints']
        assert saved[0]['compute']['cards'][0]['key'] == KEY_XT_23
        assert 'cards_chosen' in _call('/api/setup/status/')[1]['checkpoints']

    def test_404_after_setup(self, saved, probes):
        gate.mark_setup_complete()
        assert _call(SETUP_PATH)[0] == 404
        assert _call(SETUP_PATH, 'POST', _body())[0] == 404

    def test_409_while_another_mutation_runs(self, saved, probes):
        with lock.setup_mutation_lock():
            assert _call(SETUP_PATH, 'POST', _body())[0] == 409
        assert saved[1] == []

    @pytest.mark.parametrize('change', [
        {'revision': True}, {'revision': -1}, {'method': 'GPU'}, {'cards': ['pci:0000:23:00.0']},
        {'cards': ['0000:23:00.0', '0000:23:00.0'], 'shown': ['0000:23:00.0']}, {'shown': ['0000:03:00.0']},
        {'allow_manager_changes': True}, {'cards': [(0, 0x23, 0)]},
    ])
    def test_invalid_body_is_400_and_saves_nothing(self, saved, probes, change):
        status, answer = _call(SETUP_PATH, 'POST', _body(**change))
        assert status == 400 and answer['error'].endswith('.')
        assert saved[1] == [] and probes == []

    def test_deeply_nested_body_is_400_not_500(self, saved, probes):
        """About 1,000 nested arrays fit in the 4 KiB cap and make ``json.loads`` raise RecursionError."""
        raw = b'[' * 2000 + b']' * 2000
        assert len(raw) <= 4096
        status, answer = _call(SETUP_PATH, 'POST', raw=raw)
        assert (status, answer['error']) == (400, "The request body must be a JSON object.")
        assert saved[1] == [] and probes == []

    def test_get_is_served_from_the_shared_cache(self, saved, probes):
        """The wizard polls while Blender's device check runs; ten GETs run one probe, shared with /api/cards/."""
        for _ in range(10):
            assert _call(SETUP_PATH)[0] == 200
        assert len(probes) == 1
        gate.mark_setup_complete()
        assert _call('/api/cards/')[0] == 200 and len(probes) == 1
        status, _answer = _call(SETUP_PATH, 'POST', _body())
        assert status == 404 and len(probes) == 1

    def test_post_probes_fresh_even_with_a_cached_probe(self, saved, probes, monkeypatch, tmp_path):
        monkeypatch.setattr(config_store, 'get_data_dir', lambda: tmp_path)
        _call(SETUP_PATH)
        assert _call(SETUP_PATH, 'POST', _body())[0] == 200
        assert len(probes) == 2


class TestRuntime:

    @pytest.fixture(autouse=True)
    def _open(self, setup_complete, fresh_shutdown_event, bearer_header):
        self.auth = bearer_header

    def test_post_needs_password(self, saved, probes):
        assert _call('/api/control/cards', 'POST', _body())[0] == 401

    def test_post_saves(self, saved, probes):
        status, answer = _call('/api/control/cards', 'POST', _body(), self.auth)
        assert status == 200 and answer['revision'] == 1
        assert answer['message'] == ("Saved. The new graphics card choice is in use once running GPU jobs on "
                                     "removed cards finish.")
        assert rebuild.pending() is True  # live rebuild (FR-CM-6, FR-CL-4)

    def test_env_override_is_409(self, saved, probes, monkeypatch):
        monkeypatch.setenv(config_compute.ENV_METHOD, 'cpu')
        status, answer = _call('/api/control/cards', 'POST', _body(), self.auth)
        assert (status, answer['error']) == (409, cards_view.ENV_OVERRIDE_TEXT)

    def test_stale_revision_is_409(self, saved, probes):
        status, answer = _call('/api/control/cards', 'POST', _body(revision=4), self.auth)
        assert (status, answer['error']) == (409, cards_view.STALE_TEXT)

    def test_held_card_is_400(self, saved, probes):
        saved[0]['compute']['alerts'] = [{'id': 'a' * 32, 'kind': 'card_lost', 'key': KEY_XT_23}]
        status, answer = _call('/api/control/cards', 'POST', _body(), self.auth)
        assert (status, answer['error']) == (400, 'Dismiss the alert for AMD Radeon RX 7900 XT first.')

    def test_get_is_cached_and_writes_nothing(self, saved, probes):
        for _ in range(10):
            status, answer = _call('/api/cards/')
            assert status == 200 and len(answer['cards']) == 2
        assert len(probes) == 1 and saved[1] == []
