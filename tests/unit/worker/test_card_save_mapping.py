# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
How a card save maps to HTTP (FR-CW-4) and the one-family rule over the POST.

``TestSaveMapping`` replaces ``choice.save_choice`` and checks each refusal's
status and exact text. ``TestRealStore`` and ``TestCombine`` save through the
real ``store.save`` with the section in a dict (``_card_pages.fake_saved``).
"""
import logging

import pytest

from sethlans_worker_agent import config_store
from sethlans_worker_agent.cards import choice, store
from sethlans_worker_agent.config_store.io import ConfigLockTimeoutError
from sethlans_worker_agent.web_ui import cards_view
from sethlans_worker_agent.web_ui.setup import handlers_status
from tests.unit.worker._card_choice import AMD, KEY_RTX_A, KEY_RTX_B, NVIDIA, card, inv, rtx_a, xt_03, xt_23
from tests.unit.worker._card_pages import RUNTIME_POST, SETUP_PATH, body, call, fake_probes, fake_saved

ENV_TEXT = "The graphics card choice is set by environment variables on this worker."
STALE_TEXT = "The card choice was changed meanwhile (on this page or from the manager). Reload the page."
NOT_SAVED = "The card choice could not be saved; see the worker log."
COMBINE = ("Combine GPUs needs two or more cards that Blender drives the same way (CUDA/OptiX, HIP, "
           "oneAPI or Metal).")
FIELDS = {'method': 'gpu', 'gpu_mode': 'split', 'cards': ('pci:0000:23:00.0',),
          'shown': ('pci:0000:03:00.0', 'pci:0000:23:00.0'), 'revision': 0}


def _probe():
    return cards_view.Probe(inv(xt_03(), xt_23()), [])


class TestSaveMapping:

    @pytest.fixture
    def raising(self, monkeypatch):
        def install(error):
            def save_choice(*_args, **_kwargs):
                raise error
            monkeypatch.setattr(choice, 'save_choice', save_choice)
        return install

    @pytest.mark.parametrize('error, status, text', [
        pytest.param(store.EnvOverride('set by the environment'), 409, ENV_TEXT, id='env-override'),
        pytest.param(store.StaleRevision(3, 0), 409, STALE_TEXT, id='stale'),
        pytest.param(store.CardHeld('pci:0000:23:00.0', 'AMD Radeon RX 7900 XT'), 400,
                     'Dismiss the alert for AMD Radeon RX 7900 XT first.', id='held'),
        pytest.param(ValueError("The graphics card pci:0000:09:00.0 is not on this machine."), 400,
                     "The graphics card pci:0000:09:00.0 is not on this machine.", id='value-error'),
        pytest.param(ValueError(COMBINE), 400, COMBINE, id='combine'),
    ])
    def test_refusal_maps_to_status_and_text(self, raising, error, status, text):
        raising(error)
        assert cards_view.save(FIELDS, _probe(), 'settings page') == (status, {'error': text})

    @pytest.mark.parametrize('error', [
        ConfigLockTimeoutError('lock busy'), OSError('disk full'), PermissionError('denied'),
    ], ids=['lock-timeout', 'oserror', 'permission'])
    def test_write_failure_is_503_and_logged(self, raising, caplog, error):
        raising(error)
        with caplog.at_level(logging.ERROR, logger=cards_view.__name__):
            assert cards_view.save(FIELDS, _probe(), 'setup wizard') == (503, {'error': NOT_SAVED})
        [record] = [r for r in caplog.records if r.name == cards_view.__name__]
        assert record.getMessage() == "The graphics card choice from the setup wizard could not be saved."
        assert record.exc_info is not None and record.exc_info[1] is error

    def test_success_passes_fields_and_logs(self, monkeypatch, caplog):
        seen = []

        def save_choice(*args, **kwargs):
            seen.append((args, kwargs))
            return 7
        monkeypatch.setattr(choice, 'save_choice', save_choice)
        probe = _probe()
        with caplog.at_level(logging.INFO, logger=cards_view.__name__):
            assert cards_view.save(FIELDS, probe, 'settings page') == (200, {'status': 'ok', 'revision': 7})
        assert seen == [(('gpu', 'split', FIELDS['cards'], FIELDS['shown'], 0, probe.inventory), {})]
        assert [r.getMessage() for r in caplog.records if r.name == cards_view.__name__] == [(
            "Graphics card choice saved from the settings page: method gpu, GPU mode split, "
            "cards pci:0000:23:00.0; revision 7.")]


class TestRealStore:
    """``store.save`` refusals reach the page as 400 / 503 and write nothing."""

    @pytest.fixture(autouse=True)
    def _fakes(self, setup_complete, fresh_shutdown_event, bearer_header, monkeypatch):
        self.auth = bearer_header
        self.saved = fake_saved(monkeypatch)
        fake_probes(monkeypatch)

    def test_ticked_card_missing_from_fresh_probe_is_400(self):
        status, answer = call(RUNTIME_POST, 'POST', body(cards=['0000:09:00.0'], shown=['0000:09:00.0']), self.auth)
        assert (status, answer) == (400, {'error': "The graphics card pci:0000:09:00.0 is not on this machine."})
        assert self.saved[1] == []

    def test_gpu_without_a_ticked_card_is_400(self):
        status, answer = call(RUNTIME_POST, 'POST', body(cards=[]), self.auth)
        assert (status, answer) == (400, {'error': "GPU rendering needs at least one ticked graphics card."})
        assert self.saved[1] == []

    def test_lock_timeout_is_503(self, monkeypatch):
        def set_many(_pairs):
            raise ConfigLockTimeoutError('lock busy')
        monkeypatch.setattr(config_store, 'set_many', set_many)
        assert call(RUNTIME_POST, 'POST', body(), self.auth) == (503, {'error': NOT_SAVED})
        assert self.saved[0]['compute'] == {'revision': 0}


class TestSetupWriteFailure:

    def test_503_records_no_checkpoint(self, monkeypatch):
        fake_saved(monkeypatch)
        fake_probes(monkeypatch)

        def set_many(_pairs):
            raise OSError('disk full')
        monkeypatch.setattr(config_store, 'set_many', set_many)
        assert call(SETUP_PATH, 'POST', body()) == (503, {'error': NOT_SAVED})
        assert 'cards_chosen' not in handlers_status.get_current_checkpoints()


def _nvidia(key, name, blender_id, backend, device_id):
    return card(key, name, blender_id, backend, NVIDIA, device_id, 0xA1)


GTX_B = _nvidia(KEY_RTX_B, 'NVIDIA GeForce GTX 1080', 'CUDA_NVIDIA GeForce GTX 1080_0000:02:00', 'CUDA', 0x1B80)
XT_02 = card(KEY_RTX_B, 'AMD Radeon RX 7900 XT', 'HIP_AMD Radeon RX 7900 XT_0000:02:00', 'HIP', AMD, 0x744C, 0xC8)
TWO = ['0000:01:00.0', '0000:02:00.0']


class TestCombine:

    @pytest.fixture(autouse=True)
    def _open(self, setup_complete, fresh_shutdown_event, bearer_header, monkeypatch):
        self.auth = bearer_header
        self.saved = fake_saved(monkeypatch)
        self.monkeypatch = monkeypatch

    def _post(self, inventory, **changes):
        fake_probes(self.monkeypatch, inventory)
        return call(RUNTIME_POST, 'POST', body(**changes), self.auth)

    def test_two_hip_cards_are_saved_combined(self):
        status, answer = self._post(inv(xt_03(), xt_23()), gpu_mode='combined',
                                    cards=['0000:23:00.0', '0000:03:00.0'])
        assert (status, answer['revision']) == (200, 1)
        compute = self.saved[0]['compute']
        assert compute['gpu_mode'] == 'combined'
        assert [record['key'] for record in compute['cards']] == ['pci:0000:03:00.0', 'pci:0000:23:00.0']

    def test_cuda_and_optix_are_one_family(self):
        status, _answer = self._post(inv(rtx_a(), GTX_B), gpu_mode='combined', cards=TWO, shown=TWO)
        assert status == 200
        assert self.saved[0]['compute']['gpu_mode'] == 'combined'
        assert [record['key'] for record in self.saved[0]['compute']['cards']] == [KEY_RTX_A, KEY_RTX_B]

    def test_cuda_and_hip_combined_is_400(self):
        status, answer = self._post(inv(rtx_a(), XT_02), gpu_mode='combined', cards=TWO, shown=TWO)
        assert (status, answer) == (400, {'error': COMBINE})
        assert self.saved[1] == []

    def test_cuda_and_hip_split_is_saved(self):
        status, _answer = self._post(inv(rtx_a(), XT_02), gpu_mode='split', cards=TWO, shown=TWO)
        assert status == 200 and self.saved[0]['compute']['gpu_mode'] == 'split'

    def test_one_card_combined_is_stored_as_split(self, caplog):
        with caplog.at_level(logging.INFO, logger=cards_view.__name__):
            status, _answer = self._post(inv(xt_03(), xt_23()), gpu_mode='combined')
        assert status == 200 and self.saved[0]['compute']['gpu_mode'] == 'split'
        [line] = [r.getMessage() for r in caplog.records if r.name == cards_view.__name__]
        assert 'GPU mode split' in line
