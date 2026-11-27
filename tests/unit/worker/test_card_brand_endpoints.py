# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The settings POST answers 400 with the "not supported" sentence for cards of two brands (FR-PC-BR-2, AC-6).

The settings POST (``/api/control/cards``) saves through ``store.save``; a refused save writes nothing.
``--setup-apply`` checks the brands through the same ``store_rules`` (``tests/unit/worker/setup_cli``).
"""
import pytest

from sethlans_worker_agent import config_store
from tests.unit.worker._card_choice import inv, rtx_a, xt_23
from tests.unit.worker._card_pages import RUNTIME_POST, body, call, fake_probes, fake_saved

MIXED_AMD_NVIDIA = ("Graphics cards of more than one brand (AMD, NVIDIA) cannot be ticked together on one machine; "
                    "this is not supported. Tick the cards of one brand only.")
BOTH = ['0000:01:00.0', '0000:23:00.0']


@pytest.fixture
def saved(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, 'get_identity_dir', lambda: tmp_path)
    return fake_saved(monkeypatch)


@pytest.fixture
def probes(monkeypatch):
    return fake_probes(monkeypatch, inv(rtx_a(), xt_23()))


class TestRuntime:

    @pytest.fixture(autouse=True)
    def _open(self, fresh_shutdown_event, bearer_header):
        self.auth = bearer_header

    def test_settings_post_is_400(self, saved, probes):
        status, answer = call(RUNTIME_POST, 'POST', body(cards=BOTH, shown=BOTH), self.auth)
        assert (status, answer['error']) == (400, MIXED_AMD_NVIDIA)
        assert saved[1] == []

    def test_settings_post_with_method_cpu_is_400_too(self, saved, probes):
        status, answer = call(RUNTIME_POST, 'POST', body(method='cpu', cards=BOTH, shown=BOTH), self.auth)
        assert (status, answer['error']) == (400, MIXED_AMD_NVIDIA)
